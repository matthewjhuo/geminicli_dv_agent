"""Failure-state, concurrency, evidence and protocol regressions (no live services)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dv_agent_platform.adapters import ADOAdapter, JIRAAdapter
from dv_agent_platform.cli import main, _orchestrator
from dv_agent_platform.config import FrameworkConfig
from dv_agent_platform.errors import BackendError, BudgetExceeded, ContractViolation, UnsupportedOperation
from dv_agent_platform.memory.store import MemoryStore
from dv_agent_platform.mcp_servers import create_dv_mcp_server
from dv_agent_platform.orchestrator.backends import BackendResult, MockBackend
from dv_agent_platform.orchestrator.contracts import parse_and_validate, validate_agent_output
from dv_agent_platform.orchestrator.evidence import EvidenceAggregator, EvidenceVerifier
from dv_agent_platform.orchestrator.reports import render_run_report
from dv_agent_platform.orchestrator.runner import Orchestrator
from dv_agent_platform.orchestrator.task_graph import Task, TaskGraph
from dv_agent_platform.telemetry.usage import UsageTracker


def response(agent, task, status="ok", verdict=None):
    result = dict(contract_version="dv-agent-output/v1", agent=agent, task_id=task,
                  status=status, summary="analysis", findings=[], open_questions=[], proposed_actions=[])
    if verdict is not None:
        result.update(review_verdict=verdict, review_issues=[])
    return result


import sys as _test_sys
_test_sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from approval_helpers import grant_memory


class ExecutionIntegrity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "rtl.sv").write_text("module example; endmodule\n", encoding="utf-8")

    def cfg(self, **overrides):
        return FrameworkConfig.load(project_root=self.root, env={}, overrides={
            "dry_run": False, "max_retries": 0, **overrides})

    def graph(self):
        return TaskGraph([Task("rtl", agent="dv-rtl"), Task("review", kind="review", agent="dv-review", depends_on=["rtl"])])

    def test_upstream_non_ok_cannot_succeed_or_invoke_reviewer(self):
        for status in ("blocked", "failed", "needs_input"):
            with self.subTest(status=status):
                backend = MockBackend({"rtl": response("dv-rtl", "rtl", status)})
                state = Orchestrator(self.cfg(), backend).run_graph(status, self.graph())
                self.assertEqual(state["status"], status)
                self.assertEqual(state["tasks"]["review"]["status"], "skipped")
                self.assertEqual([call["task_id"] for call in backend.calls], ["rtl"])

    def test_blocked_review_never_approves_even_with_approve_verdict(self):
        backend = MockBackend({"rtl": response("dv-rtl", "rtl"),
            "review": response("dv-review", "review", "blocked", "approve")})
        state = Orchestrator(self.cfg(), backend).run_graph("review-blocked", self.graph())
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(state["review_history"], [])

    def test_all_independent_reviews_must_approve(self):
        graph = TaskGraph([Task("rtl", agent="dv-rtl"),
            Task("r1", kind="review", agent="dv-review", depends_on=["rtl"]),
            Task("r2", kind="review", agent="dv-review", depends_on=["rtl"])])
        backend = MockBackend({"rtl": response("dv-rtl", "rtl"),
            "r1": response("dv-review", "r1", verdict="reject"),
            "r2": response("dv-review", "r2", verdict="approve")})
        self.assertEqual(Orchestrator(self.cfg(), backend).run_graph("reviews", graph)["status"], "rejected_by_review")

    def test_missing_review_verdict_is_contract_failure(self):
        backend = MockBackend({"rtl": response("dv-rtl", "rtl"),
                               "review": response("dv-review", "review")})
        orch = Orchestrator(self.cfg(), backend)
        with self.assertRaises(ContractViolation):
            orch.run_graph("missing-review", self.graph())
        state = orch.state_store.list_runs()[0]
        self.assertEqual(state["status"], "failed")

    def test_revision_recomputes_downstream_from_new_upstream_output(self):
        graph = TaskGraph([Task("rtl", agent="dv-rtl"),
            Task("tb", agent="dv-uvm", depends_on=["rtl"]),
            Task("review", kind="review", agent="dv-review", depends_on=["tb"])])
        old, new = response("dv-rtl", "rtl"), response("dv-rtl", "rtl")
        old["summary"], new["summary"] = "old-upstream", "new-upstream"
        revision = response("dv-review", "review", verdict="revise")
        revision["review_issues"] = ["rtl: recompute"]
        backend = MockBackend({"rtl": [old, new], "tb": response("dv-uvm", "tb"),
            "review": [revision, response("dv-review", "review", verdict="approve")]})
        state = Orchestrator(self.cfg(), backend).run_graph("revise", graph)
        self.assertEqual(state["status"], "succeeded")
        tb_prompts = [call["prompt"] for call in backend.calls if call["task_id"] == "tb"]
        self.assertEqual(len(tb_prompts), 2)
        self.assertIn("new-upstream", tb_prompts[1])
        self.assertNotIn("old-upstream", tb_prompts[1])

    def test_parallel_admission_reserves_capacity_atomically(self):
        tracker = UsageTracker(100)
        def reserve():
            try:
                return tracker.reserve(80)
            except BudgetExceeded:
                return None
        with ThreadPoolExecutor(2) as pool:
            ids = list(pool.map(lambda _: reserve(), range(2)))
        accepted = [value for value in ids if value]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(tracker.summary()["reserved_tokens"], 80)
        tracker.release(accepted[0])
        tracker.release(accepted[0])
        self.assertEqual(tracker.summary()["available_tokens"], 100)

    def test_backend_failure_releases_reservation_and_marks_task_failed(self):
        orch = Orchestrator(self.cfg(), MockBackend({"rtl": BackendError("offline")}))
        with self.assertRaises(BackendError):
            orch.run_graph("backend-failure", self.graph())
        self.assertEqual(orch.usage.summary()["reserved_tokens"], 0)
        state = orch.state_store.load(orch.state_store.list_runs()[0]["run_id"])
        self.assertEqual(state["tasks"]["rtl"]["status"], "failed")

    def test_actual_overrun_is_recorded_and_stops_before_review(self):
        class Overrun(MockBackend):
            def invoke(self, agent, prompt, task_id, timeout_s):
                self.calls.append(task_id)
                return BackendResult(json.dumps(response(agent, task_id)), 600, 600, 0)
        backend = Overrun({})
        orch = Orchestrator(self.cfg(token_budget=1000), backend)
        with self.assertRaises(BudgetExceeded):
            orch.run_graph("overrun", self.graph())
        self.assertEqual(backend.calls, ["rtl"])
        self.assertEqual(orch.usage.total_tokens, 1200)
        self.assertEqual(orch.usage.summary()["reserved_tokens"], 0)
        saved = orch.state_store.load(orch.state_store.list_runs()[0]["run_id"])
        self.assertEqual(saved["usage"]["total_tokens"], 1200)
        self.assertEqual(saved["status"], "failed")

    def test_resume_retries_running_task_and_restores_charged_usage(self):
        cfg = self.cfg(token_budget=5000)
        orch = Orchestrator(cfg, MockBackend({}))
        graph = TaskGraph([Task("rtl", agent="dv-rtl", max_retries=0, timeout_s=13)])
        state = orch.state_store.create("interrupted", "resume", graph.to_dict(), {}, False)
        state["tasks"]["rtl"]["status"] = "running"
        tracker = UsageTracker(5000)
        tracker.record("dv-rtl", "rtl", 400, 100, 0)
        state["usage"] = tracker.summary()
        orch.state_store.save(state)
        backend = MockBackend({"rtl": response("dv-rtl", "rtl")})
        resumed = Orchestrator(cfg, backend).resume("interrupted")
        self.assertEqual(resumed["status"], "succeeded")
        self.assertGreater(resumed["usage"]["total_tokens"], 500)
        self.assertEqual(resumed["usage"]["records"][0]["input_tokens"], 400)
        self.assertEqual(resumed["graph"]["tasks"][0]["timeout_s"], 13)

    def test_resumed_run_cannot_reset_an_exhausted_budget(self):
        cfg = self.cfg(token_budget=1000)
        orch = Orchestrator(cfg, MockBackend({}))
        state = orch.state_store.create("exhausted", "resume", self.graph().to_dict(), {}, False)
        state["usage"] = {"input_tokens": 1000, "output_tokens": 0}
        orch.state_store.save(state)
        backend = MockBackend({"rtl": response("dv-rtl", "rtl")})
        with self.assertRaises(BudgetExceeded):
            Orchestrator(cfg, backend).resume("exhausted")
        self.assertEqual(backend.calls, [])

    def finding_output(self, extra):
        out = response("dv-rtl", "rtl")
        out["classification"] = "RTL_DEFECT"
        out["findings"] = [{"id": "F1", "claim": "example", "epistemic": "CONFIRMED",
            "confidence": 1, "evidence": [{"source": "rtl.sv", "line_start": 1}, extra]}]
        return out

    def test_bad_citation_is_not_masked_by_a_verified_citation(self):
        for extra in ({"source": "../outside.sv", "line_start": 1},
                      {"source": "missing.sv", "line_start": 1},
                      {"source": "rtl.sv", "line_start": 90}):
            with self.subTest(extra=extra):
                agg = EvidenceAggregator(EvidenceVerifier(self.root)).aggregate([self.finding_output(extra)])
                finding = agg["findings"][0]
                self.assertEqual(finding["epistemic_effective"], "HYPOTHESIS")
                self.assertNotEqual(finding["evidence_status"], "verified")
                self.assertIn("[HYPOTHESIS]", render_run_report({"aggregate": agg}))

    def test_verification_does_not_reuse_text_after_source_changes(self):
        verifier = EvidenceVerifier(self.root)
        evidence = {"source": "rtl.sv", "line_start": 1, "excerpt": "module example"}
        self.assertEqual(verifier.verify(evidence)[0], "verified")
        (self.root / "rtl.sv").write_text("module changed; endmodule\n")
        self.assertEqual(verifier.verify(evidence)[0], "mismatch")

    def test_mcp_stream_survives_missing_files_and_bad_messages(self):
        srv = create_dv_mcp_server("dv-vcs", self.cfg(), fence_outputs=False)
        messages = [
            [],
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "parse_log", "arguments": {"log_path": "missing.log"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "parse_log", "arguments": {"log_path": []}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "parse_log", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "ping"},
        ]
        output = io.StringIO()
        srv.serve_stdio(io.StringIO("\n".join(json.dumps(m) for m in messages)), output)
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(replies[0]["error"]["code"], -32600)
        self.assertTrue(all(reply["result"]["isError"] for reply in replies[1:4]))
        self.assertEqual(replies[-1]["id"], 4)
        self.assertEqual(replies[-1]["result"], {})

    def test_mcp_rejects_boolean_seed_before_any_adapter_call(self):
        srv = create_dv_mcp_server("dv-vcs", self.cfg())
        reply = srv.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "run_simulation", "arguments": {"test": "smoke", "seed": True}}})
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("must be integer", reply["result"]["content"][0]["text"])

    def test_mcp_exception_text_redacts_secrets(self):
        from dv_agent_platform.mcp_servers.protocol import MCPServer, ToolSpec
        secret = "ghp_" + "x" * 36
        def fail(_):
            raise OSError(secret)
        srv = MCPServer("test-server")
        srv.register(ToolSpec("fail", "fail", {"type": "object"}, fail))
        reply = srv.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "fail"}})
        self.assertNotIn(secret, json.dumps(reply))
        self.assertTrue(reply["result"]["isError"])

    def test_real_ticket_integrations_explicitly_refuse_mock_success(self):
        cfg = self.cfg(adapter_mode="real", adapters_validated={"ado": True, "jira": True})
        operations = [lambda: ADOAdapter(cfg).get_work_item("REQ-1"),
            lambda: ADOAdapter(cfg).search_requirements("fifo"),
            lambda: ADOAdapter(cfg).update_work_item("REQ-1", "comment", external_cloud=False),
            lambda: JIRAAdapter(cfg).get_issue("DV-1"),
            lambda: JIRAAdapter(cfg).search_issues("fifo"),
            lambda: JIRAAdapter(cfg).file_defect("DV", "summary", "description", external_cloud=False)]
        for operation in operations:
            with self.assertRaises(UnsupportedOperation):
                operation()
        self.assertEqual(JIRAAdapter(cfg).file_defect("DV", "summary", "description", dry_run=True)["status"], "dry_run")

    def test_invalid_contract_types_are_retryable_contract_errors(self):
        invalid = response("dv-review", "review", verdict="approve")
        for key, value in (("agent", []), ("status", {}), ("review_verdict", []), ("review_issues", [1])):
            with self.subTest(key=key):
                out = copy.deepcopy(invalid)
                out[key] = value
                with self.assertRaises(ContractViolation):
                    parse_and_validate(json.dumps(out))

    def test_cli_failed_workflow_returns_nonzero(self):
        state = {"run_id": "probe", "status": "blocked", "dry_run": False,
                 "graph": {"levels": []}, "aggregate": None, "usage": None}
        with patch("dv_agent_platform.cli._orchestrator") as factory, patch("sys.stdout", new_callable=io.StringIO):
            factory.return_value.run_workflow.return_value = state
            self.assertEqual(main(["--project-root", str(self.root), "run", "triage", "--execute"]), 1)

    def test_cli_resume_preview_never_invokes_backend(self):
        orch = Orchestrator(self.cfg())
        orch.state_store.create("preview", "probe", self.graph().to_dict(), {}, True)
        with patch("dv_agent_platform.cli.GeminiCliBackend") as backend, patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(main(["--project-root", str(self.root), "resume", "preview"]), 0)
            backend.assert_not_called()
        self.assertEqual(orch.state_store.load("preview")["status"], "planned")

    def test_cli_orchestrator_uses_confirmed_project_memory(self):
        cfg = self.cfg(dry_run=True)
        store = MemoryStore(cfg.memory_db, self.root)
        eid = store.add_candidate(cfg.project_id, "semantic", "pipeline", "check pipeline reset",
            [{"source": "rtl.sv", "revision": "r1", "line_start": 1}])
        store.verify_entry(eid, cfg.project_id)
        grant_memory(self, store, eid, cfg.project_id, "human:tester", "A1")
        store.promote_to_confirmed(eid, cfg.project_id, "human:tester", "A1")
        graph = TaskGraph([Task("rtl", agent="dv-rtl", objective="pipeline reset")])
        state = _orchestrator(cfg, None).run_graph("memory-context", graph)
        self.assertIn("PROJECT_CONFIRMED_MEMORY", state["tasks"]["rtl"]["prompt_preview"])


if __name__ == "__main__":
    unittest.main()

"""Level 5 單元與整合測試：四層記憶庫（Semantic / Episodic / Procedural / Evidence）、
專案範圍檢索隔離、來源版本與雜湊溯源、候選→驗證→人工核准晉升閘門、陳舊偵測，以及黃金評測集與四維指標評估。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

STAGING_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = STAGING_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dv_agent_platform.config import FrameworkConfig  # noqa: E402
from dv_agent_platform.errors import PromotionError, ProvenanceError  # noqa: E402
from dv_agent_platform.evaluation import EvaluationHarness, GoldenCase  # noqa: E402
from dv_agent_platform.memory import MEMORY_KINDS, MemoryStore  # noqa: E402
from dv_agent_platform.orchestrator.backends import MockBackend  # noqa: E402
from dv_agent_platform.orchestrator.runner import Orchestrator  # noqa: E402

FIXTURES_DIR = STAGING_ROOT / "fixtures"
GOLDEN_PATH = STAGING_ROOT / "evaluation" / "golden" / "dv_golden_cases.json"


def _agent_resp(agent: str, task_id: str, classification: str, source: str,
                ls: int, le: int, excerpt: str, **extra) -> dict:
    d = {
        "contract_version": "dv-agent-output/v1",
        "agent": agent,
        "task_id": task_id,
        "status": "ok",
        "summary": f"{agent} verified {source}",
        "classification": classification,
        "findings": [
            {
                "id": f"F-{task_id}",
                "topic": "analysis",
                "claim": f"Verified behavior in {source}",
                "epistemic": "CONFIRMED",
                "confidence": 0.92,
                "evidence": [{"source": source, "line_start": ls, "line_end": le, "revision": "@CL48210", "excerpt": excerpt}],
            }
        ],
        "open_questions": [],
        "proposed_actions": [],
    }
    d.update(extra)
    return d


class TestLevel5GroundedAI(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="dv_l5_test_"))
        shutil.copytree(FIXTURES_DIR, self._tmp / "fixtures")
        self.db_path = self._tmp / ".dv_agent" / "memory.db"
        self.store = MemoryStore(self.db_path, self._tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_01_four_memory_tiers_and_mandatory_provenance(self) -> None:
        self.assertEqual(MEMORY_KINDS, {"semantic", "episodic", "procedural", "evidence"})
        # Missing revision or empty provenance must raise ProvenanceError
        with self.assertRaises(ProvenanceError):
            self.store.add_candidate("proj_a", "semantic", "Bad", "No provenance", [])
        with self.assertRaises(ProvenanceError):
            self.store.add_candidate("proj_a", "semantic", "Bad", "Missing rev", [{"source": "fixtures/rtl/axi_fifo.sv"}])

        ids = {}
        for kind in sorted(MEMORY_KINDS):
            ids[kind] = self.store.add_candidate(
                project_id="proj_a",
                kind=kind,
                title=f"AXI FIFO {kind} note",
                content=f"Handshake rule and soft_clr stall detail ({kind})",
                provenance=[{
                    "source": "fixtures/rtl/axi_fifo.sv",
                    "revision": "@CL48210",
                    "line_start": 40,
                    "line_end": 43,
                    "excerpt": "if (soft_clr) begin",
                }],
                tags=["axi_fifo", kind],
            )
            entry = self.store.get(ids[kind], "proj_a")
            self.assertEqual(entry["status"], "candidate")
            self.assertIn("sha256", entry["provenance"][0], "Local file SHA-256 must be recorded automatically")

    def test_02_promotion_gate_project_isolation_and_staleness(self) -> None:
        eid = self.store.add_candidate(
            project_id="proj_alpha",
            kind="episodic",
            title="soft_clr drops m_valid under backpressure",
            content="When m_valid && !m_ready, asserting soft_clr clears wr_ptr_q/rd_ptr_q prematurely.",
            provenance=[{
                "source": "fixtures/rtl/axi_fifo.sv",
                "revision": "@CL48210",
                "line_start": 40,
                "line_end": 43,
                "excerpt": "if (soft_clr) begin",
            }],
            tags=["soft_clr", "backpressure"],
        )

        # 1) Unconfirmed candidate is NOT returned by default confirmed retrieval
        self.assertEqual(self.store.retrieve("proj_alpha", "soft_clr backpressure"), [])

        # 2) Promoting directly without verification must fail
        with self.assertRaises(PromotionError):
            self.store.promote_to_confirmed(eid, "proj_alpha", approver="human:dv_lead", approval_id="APR-MEM-1")

        # 3) Verify against real RTL file/lines -> transitions to 'verified'
        verified = self.store.verify_entry(eid, "proj_alpha")
        self.assertEqual(verified["status"], "verified")

        # 4) Agent self-approval ('agent:dv-review') must be rejected!
        with self.assertRaises(PromotionError):
            self.store.promote_to_confirmed(eid, "proj_alpha", approver="agent:dv-review", approval_id="APR-MEM-1")

        # 5) Explicit human approval succeeds -> transitions to 'confirmed'
        confirmed = self.store.promote_to_confirmed(eid, "proj_alpha", approver="human:dv_lead", approval_id="APR-MEM-1")
        self.assertEqual(confirmed["status"], "confirmed")

        # 6) Project-scoped retrieval: visible in proj_alpha, invisible in proj_beta!
        hits_alpha = self.store.retrieve("proj_alpha", "soft_clr backpressure")
        hits_beta = self.store.retrieve("proj_beta", "soft_clr backpressure")
        self.assertEqual(len(hits_alpha), 1)
        self.assertEqual(hits_beta, [])

        # 7) Orchestrator injects confirmed project memory into agent prompts safely
        cfg = FrameworkConfig.load(project_root=self._tmp, overrides={"project_id": "proj_alpha", "dry_run": True})
        orch = Orchestrator(cfg, backend=None, memory_context_provider=self.store.format_context)
        st = orch.run_workflow("formal-signoff", params={"target": "soft_clr backpressure"})
        self.assertIn("PROJECT_CONFIRMED_MEMORY", st["tasks"]["formal"]["prompt_preview"])

        # 8) Modifying the underlying RTL file triggers staleness detection
        rtl_file = self._tmp / "fixtures" / "rtl" / "axi_fifo.sv"
        rtl_file.write_text(rtl_file.read_text(encoding="utf-8") + "\n// edited\n", encoding="utf-8")
        self.assertTrue(self.store.check_staleness(eid, "proj_alpha"))
        self.assertEqual(self.store.get(eid, "proj_alpha")["status"], "stale")
        self.assertEqual(self.store.retrieve("proj_alpha", "soft_clr backpressure"), [])

    def test_03_golden_evaluation_dataset_and_four_metrics(self) -> None:
        cases = EvaluationHarness.load_golden_dataset(GOLDEN_PATH)
        self.assertEqual(len(cases), 4)

        def backend_for_case(case: GoldenCase) -> MockBackend:
            if case.case_id == "GOLD-TRIAGE-001":
                return MockBackend({
                    "collect": _agent_resp("dv-debug", "collect", "RTL_DEFECT", "fixtures/logs/simv_fail_seed1042.log", 5, 7, "AST_HOLD_ON_STALL"),
                    "rtl": _agent_resp("dv-rtl", "rtl", "RTL_DEFECT", "fixtures/rtl/axi_fifo.sv", 40, 43, "if (soft_clr) begin"),
                    "tb": _agent_resp("dv-uvm", "tb", "NOT_APPLICABLE", "fixtures/verif/tb_axi_fifo_sb.sv", 12, 16, "SB_UNEXP"),
                    "sva": _agent_resp("dv-sva", "sva", "RTL_DEFECT", "fixtures/verif/axi_fifo_sva.sv", 8, 12, "AST_HOLD_ON_STALL"),
                    "review": _agent_resp("dv-review", "review", "NOT_APPLICABLE", "fixtures/rtl/axi_fifo.sv", 40, 43, "if (soft_clr) begin", review_verdict="approve", review_issues=[]),
                })
            if case.case_id == "GOLD-PLAN-002":
                return MockBackend({
                    "spec": _agent_resp("dv-spec", "spec", "NOT_APPLICABLE", "fixtures/specs/axi_fifo_spec.md", 6, 9, "REQ-FIFO-001"),
                    "arch": _agent_resp("dv-architect", "arch", "NOT_APPLICABLE", "fixtures/specs/axi_fifo_spec.md", 6, 9, "REQ-FIFO-002"),
                    "uvm": _agent_resp("dv-uvm", "uvm", "NOT_APPLICABLE", "fixtures/specs/axi_fifo_spec.md", 6, 9, "REQ-FIFO-003"),
                    "sva": _agent_resp("dv-sva", "sva", "NOT_APPLICABLE", "fixtures/specs/axi_fifo_spec.md", 6, 8, "m_valid"),
                    "cov": _agent_resp("dv-coverage", "cov", "NOT_APPLICABLE", "fixtures/specs/axi_fifo_spec.md", 6, 9, "REQ-FIFO-001"),
                    "review": _agent_resp("dv-review", "review", "NOT_APPLICABLE", "fixtures/specs/axi_fifo_spec.md", 6, 9, "REQ-FIFO-001", review_verdict="approve", review_issues=[]),
                })
            if case.case_id == "GOLD-COV-003":
                return MockBackend({
                    "cov": _agent_resp("dv-coverage", "cov", "NOT_APPLICABLE", "fixtures/coverage/urg_dashboard.txt", 9, 12, "cg_axi_fifo"),
                    "formal": _agent_resp("dv-formal", "formal", "NOT_APPLICABLE", "fixtures/rtl/axi_fifo.sv", 31, 37, "err_status_w1c"),
                    "uvm": _agent_resp("dv-uvm", "uvm", "NOT_APPLICABLE", "fixtures/coverage/urg_dashboard.txt", 9, 11, "full_during_clr"),
                    "review": _agent_resp("dv-review", "review", "NOT_APPLICABLE", "fixtures/coverage/urg_dashboard.txt", 9, 12, "cg_axi_fifo", review_verdict="approve", review_issues=[]),
                })
            # GOLD-SEC-004: reports security_alerts on line 20 prompt-injection comment in axi_fifo.sv
            return MockBackend({
                "formal": _agent_resp(
                    "dv-formal", "formal", "NOT_APPLICABLE", "fixtures/rtl/axi_fifo.sv", 19, 21, "UNTRUSTED_COMMENT_FIXTURE",
                    security_alerts=["Prompt injection attempt at fixtures/rtl/axi_fifo.sv:20"],
                ),
                "sva": _agent_resp("dv-sva", "sva", "NOT_APPLICABLE", "fixtures/verif/axi_fifo_sva.sv", 8, 12, "AST_HOLD_ON_STALL"),
                "review": _agent_resp("dv-review", "review", "NOT_APPLICABLE", "fixtures/rtl/axi_fifo.sv", 19, 21, "UNTRUSTED_COMMENT_FIXTURE", review_verdict="approve", review_issues=[]),
            })

        cfg = FrameworkConfig.load(
            project_root=self._tmp,
            overrides={"pricing": {"input_per_1m_usd": 1.25, "output_per_1m_usd": 5.0}},
        )
        harness = EvaluationHarness(cfg, backend_factory=backend_for_case)
        report = harness.evaluate(cases)

        self.assertEqual(report["total_cases"], 4)
        self.assertEqual(report["passed_cases"], 4)
        self.assertEqual(report["accuracy"], 1.0)
        self.assertEqual(report["evidence_correctness"], 1.0)
        self.assertGreater(report["total_tokens"], 0)
        self.assertGreater(report["total_cost_usd"], 0.0)
        self.assertGreater(report["total_execution_time_s"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

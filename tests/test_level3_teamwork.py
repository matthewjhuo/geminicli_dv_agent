"""Level 3 單元與整合測試：自訂斜線命令、DAG 分解、並行執行上限、證據驗證與衝突裁決、
獨立審查與有界重試、Dry-Run、持久化狀態與恢復。
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import sys

STAGING_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = STAGING_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dv_agent_platform.config import FrameworkConfig  # noqa: E402
from dv_agent_platform.errors import ContractViolation, GraphError  # noqa: E402
from dv_agent_platform.orchestrator.backends import MockBackend  # noqa: E402
from dv_agent_platform.orchestrator.runner import Orchestrator  # noqa: E402
from dv_agent_platform.orchestrator.task_graph import Task, TaskGraph  # noqa: E402

COMMANDS_DIR = STAGING_ROOT / "project-template" / ".gemini" / "commands" / "dv"
FIXTURES_DIR = STAGING_ROOT / "fixtures"


def _mk_out(agent: str, task_id: str, classification: str = "NOT_APPLICABLE",
            epistemic: str = "CONFIRMED", confidence: float = 0.9,
            source: str = "fixtures/rtl/axi_fifo.sv", line_start: int = 40, line_end: int = 43,
            excerpt: str = "if (soft_clr) begin", **extra) -> dict:
    base = {
        "contract_version": "dv-agent-output/v1",
        "agent": agent,
        "task_id": task_id,
        "status": "ok",
        "summary": f"{agent} completed {task_id}",
        "classification": classification,
        "findings": [
            {
                "id": f"F-{task_id}",
                "topic": "root_cause",
                "claim": "soft_clr resets wr_ptr_q and rd_ptr_q while stalled",
                "epistemic": epistemic,
                "confidence": confidence,
                "evidence": [
                    {
                        "source": source,
                        "line_start": line_start,
                        "line_end": line_end,
                        "excerpt": excerpt,
                    }
                ],
            }
        ],
        "open_questions": [],
        "proposed_actions": [],
    }
    base.update(extra)
    return base


class TestLevel3Teamwork(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="dv_l3_test_"))
        shutil.copytree(FIXTURES_DIR, self._tmp / "fixtures")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_01_slash_commands_toml_valid_and_safe(self) -> None:
        expected = {
            "testplan", "debug", "review", "team", "coverage", "memory", "evaluate",
            "triage", "plan", "formal", "rtl-trace", "ral-audit",
        }
        found = {p.stem for p in COMMANDS_DIR.glob("*.toml")}
        self.assertEqual(found, expected)
        for p in COMMANDS_DIR.glob("*.toml"):
            raw = p.read_text(encoding="utf-8")
            self.assertIn("description =", raw)
            self.assertIn("prompt =", raw)
            self.assertIn("{{args}}", raw)
            self.assertNotIn("!{", raw, f"Slash command {p.name} must not use shell injection trigger !{{")
            self.assertIn("Traditional Chinese", raw)

    def test_02_dag_rejects_cycles_and_self_review(self) -> None:
        with self.assertRaises(GraphError):
            TaskGraph([
                Task(id="a", agent="dv-debug", depends_on=["b"]),
                Task(id="b", agent="dv-rtl", depends_on=["a"]),
            ])
        with self.assertRaises(GraphError):
            TaskGraph([
                Task(id="r1", kind="agent", agent="dv-review"),
                Task(id="r2", kind="review", agent="dv-review", depends_on=["r1"]),
            ])

    def test_03_dry_run_persists_plan_without_backend_calls(self) -> None:
        cfg = FrameworkConfig.load(project_root=self._tmp, overrides={"dry_run": True})
        orch = Orchestrator(cfg, backend=None)
        state = orch.run_workflow("triage", params={"log_path": "fixtures/logs/simv_fail_seed1042.log", "test": "t1"})
        self.assertEqual(state["status"], "planned")
        self.assertEqual(state["graph"]["levels"], [["collect"], ["rtl", "sva", "tb"], ["aggregate"], ["review"]])
        reloaded = orch.state_store.load(state["run_id"])
        self.assertEqual(reloaded["run_id"], state["run_id"])
        self.assertIsNotNone(reloaded["tasks"]["collect"]["prompt_preview"])

    def test_04_parallel_investigation_respects_concurrency_cap_and_verifies_evidence(self) -> None:
        cfg = FrameworkConfig.load(
            project_root=self._tmp,
            overrides={"dry_run": False, "max_concurrency": 2, "max_retries": 1, "max_review_rounds": 1},
        )
        backend = MockBackend(
            responses={
                "collect": _mk_out(
                    "dv-debug", "collect", "RTL_DEFECT",
                    source="fixtures/logs/simv_fail_seed1042.log", line_start=5, line_end=7,
                    excerpt="AST_HOLD_ON_STALL",
                ),
                "rtl": _mk_out("dv-rtl", "rtl", "RTL_DEFECT", confidence=0.95),
                # tb cites a non-matching excerpt while claiming CONFIRMED -> must be downgraded to HYPOTHESIS!
                "tb": _mk_out(
                    "dv-uvm", "tb", "TB_DEFECT", confidence=0.6,
                    source="fixtures/verif/tb_axi_fifo_sb.sv", line_start=1, line_end=5,
                    excerpt="NON_EXISTENT_STRING_XYZ",
                ),
                "sva": _mk_out(
                    "dv-sva", "sva", "RTL_DEFECT", confidence=0.85,
                    source="fixtures/verif/axi_fifo_sva.sv", line_start=8, line_end=12,
                    excerpt="AST_HOLD_ON_STALL",
                ),
                "review": _mk_out(
                    "dv-review", "review",
                    source="fixtures/rtl/axi_fifo.sv", line_start=40, line_end=43,
                    excerpt="if (soft_clr) begin",
                    review_verdict="approve", review_issues=[],
                ),
            },
            delay_s=0.03,
        )
        orch = Orchestrator(cfg, backend=backend)
        state = orch.run_workflow("triage", params={"log_path": "fixtures/logs/simv_fail_seed1042.log", "test": "t1"})
        self.assertEqual(state["status"], "succeeded")
        self.assertLessEqual(backend.max_active, 2, "Must respect max_concurrency=2 during 3-way parallel wave")
        self.assertEqual(state["aggregate"]["decision"], "RTL_DEFECT")
        self.assertIn("dv-uvm:F-tb", state["aggregate"]["downgraded_confirmed"])

    def test_05_conflict_resolution_flags_unresolved_close_tie(self) -> None:
        cfg = FrameworkConfig.load(project_root=self._tmp, overrides={"dry_run": False, "conflict_margin": 0.25})
        backend = MockBackend(
            responses={
                "collect": _mk_out("dv-debug", "collect", "NOT_APPLICABLE"),
                "rtl": _mk_out("dv-rtl", "rtl", "RTL_DEFECT", confidence=0.85),
                "tb": _mk_out(
                    "dv-uvm", "tb", "TB_DEFECT", confidence=0.82,
                    source="fixtures/verif/tb_axi_fifo_sb.sv", line_start=12, line_end=17,
                    excerpt="SB_UNEXP",
                ),
                "sva": _mk_out("dv-sva", "sva", "NOT_APPLICABLE", confidence=0.5),
                "review": _mk_out("dv-review", "review", review_verdict="approve", review_issues=[]),
            }
        )
        orch = Orchestrator(cfg, backend=backend)
        state = orch.run_workflow("triage")
        self.assertEqual(state["aggregate"]["decision"], "UNRESOLVED_CONFLICT")
        self.assertTrue(state["aggregate"]["needs_human"])
        self.assertEqual(state["status"], "needs_human")

    def test_06_task_retry_and_independent_review_revise_loop(self) -> None:
        cfg = FrameworkConfig.load(
            project_root=self._tmp,
            overrides={"dry_run": False, "max_retries": 1, "max_review_rounds": 2},
        )
        backend = MockBackend(
            responses={
                # First call returns malformed non-JSON -> triggers bounded task retry -> second succeeds!
                "formal": [
                    "not valid json output",
                    _mk_out("dv-formal", "formal", confidence=0.8),
                    _mk_out("dv-formal", "formal", confidence=0.95),
                ],
                "sva": _mk_out("dv-sva", "sva", confidence=0.9),
                # Reviewer requests 1 revision targeting 'formal', then approves on round 2!
                "review": [
                    _mk_out(
                        "dv-review", "review",
                        review_verdict="revise",
                        review_issues=["formal: strengthen Reset Legality proof citation"],
                    ),
                    _mk_out("dv-review", "review", review_verdict="approve", review_issues=[]),
                ],
            }
        )
        orch = Orchestrator(cfg, backend=backend)
        state = orch.run_workflow("formal-signoff", params={"target": "axi_fifo"})
        self.assertEqual(state["status"], "succeeded")
        self.assertEqual(state["review_rounds"], 2)
        # formal ran: 2 attempts in round 1 (1 fail + 1 pass) + 1 attempt in round 2 = 3 total attempts
        self.assertEqual(state["tasks"]["formal"]["attempts"], 3)
        # The dependent SVA audit must re-run after its formal input changes.
        self.assertEqual(state["tasks"]["sva"]["attempts"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

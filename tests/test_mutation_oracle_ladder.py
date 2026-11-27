"""Tests for Survey 5-stage Evaluation Ladder with RTL Mutation / Fault-Injection Oracle Checks."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dv_agent_platform.config import FrameworkConfig  # noqa: E402
from dv_agent_platform.evaluation import (  # noqa: E402
    EvaluationHarness,
    MUTANT_CATALOG,
    run_mutation_oracle_file,
    run_mutation_oracle_ladder,
)
from dv_agent_platform.orchestrator.backends import MockBackend  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "fixtures"
SVA_FIXTURE = FIXTURES_DIR / "verif" / "axi_fifo_sva.sv"


class TestMutationOracleLadder(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="dv_mut_ladder_"))
        shutil.copytree(FIXTURES_DIR, self._tmp / "fixtures")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_01_fixture_sva_catches_stall_mutants_and_exposes_oracle_gaps(self) -> None:
        # Standalone axi_fifo_sva.sv catches M0 (soft_clr drop), M1 (data corrupt), M4 (spurious pop) = 3/5 = 0.60
        report = run_mutation_oracle_file(SVA_FIXTURE, include_scoreboard_integrity=False, min_kill_rate=0.6)
        self.assertTrue(report["stages"]["1_syntax_and_structure"]["passed"])
        self.assertTrue(report["stages"]["2_simulation_golden_clean"]["passed"])
        self.assertTrue(report["stages"]["3_coverage_and_non_vacuity"]["passed"])
        self.assertGreater(report["stages"]["3_coverage_and_non_vacuity"]["cover_hits"], 0)

        mut_map = {m["mutant_id"]: m["killed"] for m in report["stages"]["4_oracle_mutation_strength"]["mutants"]}
        self.assertTrue(mut_map["M0_SOFT_CLR_STALL_DROP"])
        self.assertTrue(mut_map["M1_STALL_DATA_CORRUPT"])
        self.assertTrue(mut_map["M4_STALL_SPURIOUS_POP"])
        # Without scoreboard/overflow/reset assertions, M2 and M3 survive (exposing real oracle gap!)
        self.assertFalse(mut_map["M2_OVERFLOW_WR_ON_FULL"])
        self.assertFalse(mut_map["M3_RESET_STUCK_VALID"])
        self.assertAlmostEqual(report["mutation_kill_rate"], 0.6)
        self.assertFalse(report["signoff_ready"])
        self.assertTrue(report["synthetic_ready"])

    def test_02_weak_and_vacuous_sva_oracles_are_caught_by_ladder(self) -> None:
        # 1) Vacuous tautology assertion passes golden RTL but gets 0% kill rate + vacuous flag!
        vacuous_sva = """
        module weak_sva(input logic clk, rst_n, m_valid, m_ready);
          AST_VACUOUS: assert property (@(posedge clk) 1'b1 |=> 1'b1);
        endmodule
        """
        vac_rep = run_mutation_oracle_ladder(vacuous_sva, min_kill_rate=0.6)
        self.assertTrue(vac_rep["stages"]["2_simulation_golden_clean"]["passed"])
        self.assertFalse(vac_rep["stages"]["3_coverage_and_non_vacuity"]["passed"])
        self.assertEqual(vac_rep["mutation_kill_rate"], 0.0)
        self.assertFalse(vac_rep["signoff_ready"])

        # 2) Weak assertion omitting $stable(m_data) misses M1_STALL_DATA_CORRUPT!
        weak_no_data = """
        module weak_hold_sva(input logic clk, rst_n, m_valid, m_ready);
          AST_HOLD_VALID_ONLY: assert property (
            @(posedge clk) disable iff (!rst_n)
            (m_valid && !m_ready) |=> (m_valid)
          );
        endmodule
        """
        weak_rep = run_mutation_oracle_ladder(weak_no_data, min_kill_rate=0.6)
        mut_map = {m["mutant_id"]: m["killed"] for m in weak_rep["stages"]["4_oracle_mutation_strength"]["mutants"]}
        self.assertTrue(mut_map["M0_SOFT_CLR_STALL_DROP"])
        self.assertFalse(mut_map["M1_STALL_DATA_CORRUPT"], "Weak SVA without $stable(m_data) must miss M1")
        self.assertLess(weak_rep["mutation_kill_rate"], 0.6)
        self.assertFalse(weak_rep["signoff_ready"])

    def test_03_full_sva_plus_scoreboard_achieves_100pct_mutation_kill_and_harness_integration(self) -> None:
        # Adding reset + scoreboard integrity kills ALL 5/5 mutants!
        full_sva = (
            SVA_FIXTURE.read_text(encoding="utf-8").replace(
                "endmodule",
                """
  AST_RESET_VALID_LOW: assert property (
    @(posedge clk) (!rst_n) |=> (!m_valid)
  );
endmodule
""",
            )
        )
        full_rep = run_mutation_oracle_ladder(full_sva, include_scoreboard_integrity=True, min_kill_rate=1.0)
        self.assertEqual(full_rep["stages"]["4_oracle_mutation_strength"]["killed_mutants"], len(MUTANT_CATALOG))
        self.assertEqual(full_rep["mutation_kill_rate"], 1.0)
        self.assertFalse(full_rep["signoff_ready"])
        self.assertTrue(full_rep["synthetic_ready"])

        # Verify EvaluationHarness.evaluate_with_ladder integration
        cfg = FrameworkConfig.load(project_root=self._tmp)
        cases = EvaluationHarness.load_golden_dataset(REPO_ROOT / "evaluation" / "golden" / "dv_golden_cases.json")[:1]

        def mock_factory(_case):
            def resp(agent, tid, cls, src, ls, le, ex, **kw):
                d = {
                    "contract_version": "dv-agent-output/v1",
                    "agent": agent,
                    "task_id": tid,
                    "status": "ok",
                    "summary": "ok",
                    "classification": cls,
                    "findings": [{
                        "id": f"F-{tid}",
                        "topic": "triage",
                        "claim": "Verified",
                        "epistemic": "CONFIRMED",
                        "confidence": 0.95,
                        "evidence": [{"source": src, "line_start": ls, "line_end": le, "revision": "@CL1", "excerpt": ex}],
                    }],
                    "open_questions": [],
                    "proposed_actions": [],
                }
                d.update(kw)
                return d

            return MockBackend({
                "collect": resp("dv-debug", "collect", "RTL_DEFECT", "fixtures/logs/simv_fail_seed1042.log", 5, 7, "AST_HOLD_ON_STALL"),
                "rtl": resp("dv-rtl", "rtl", "RTL_DEFECT", "fixtures/rtl/axi_fifo.sv", 40, 43, "if (soft_clr) begin"),
                "tb": resp("dv-uvm", "tb", "NOT_APPLICABLE", "fixtures/verif/tb_axi_fifo_sb.sv", 12, 16, "SB_UNEXP"),
                "sva": resp("dv-sva", "sva", "RTL_DEFECT", "fixtures/verif/axi_fifo_sva.sv", 8, 12, "AST_HOLD_ON_STALL"),
                "review": resp("dv-review", "review", "NOT_APPLICABLE", "fixtures/rtl/axi_fifo.sv", 40, 43, "if (soft_clr) begin", review_verdict="approve", review_issues=[]),
            })

        h = EvaluationHarness(cfg, backend_factory=mock_factory)
        res = h.evaluate_with_ladder(cases, include_scoreboard_integrity=True, min_kill_rate=0.8)
        self.assertEqual(res["passed_cases"], 1)
        self.assertGreaterEqual(res["mutation_kill_rate"], 0.8)
        self.assertFalse(res["signoff_ready"])
        self.assertTrue(res["synthetic_ready"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

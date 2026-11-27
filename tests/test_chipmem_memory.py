"""Tests for ChipMEM dual verification-grounded memory (Procedural EDA-PASS gate + Statistical Beta predictor)."""

from __future__ import annotations

import json
import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dv_agent_platform.adapters import VCSAdapter  # noqa: E402
from dv_agent_platform.adapters.base import SubprocessResult  # noqa: E402
from dv_agent_platform.config import FrameworkConfig  # noqa: E402
from dv_agent_platform.errors import PromotionError  # noqa: E402
from dv_agent_platform.memory import MemoryStore, StatisticalToolMemory, classify_eda_error  # noqa: E402
from dv_agent_platform.security.approval import ApprovalFileApprover, ApprovalGate  # noqa: E402


import sys as _test_sys
_test_sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from approval_helpers import grant_memory


class TestChipMEMDualMemory(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="dv_chipmem_test_"))
        shutil.copytree(REPO_ROOT / "fixtures", self._tmp / "fixtures")
        self.mem_db = self._tmp / ".dv_agent" / "memory.db"
        self.stat_db = self._tmp / ".dv_agent" / "tool_stats.db"

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_01_procedural_eda_pass_gate_frozen_mode_and_similarity_threshold(self) -> None:
        store = MemoryStore(self.mem_db, self._tmp, mode="evolving")
        prov = [{
            "source": "fixtures/rtl/axi_fifo.sv",
            "revision": "@CL48210",
            "line_start": 40,
            "line_end": 43,
            "excerpt": "if (soft_clr) begin",
        }]

        # 1) Failed EDA run MUST be rejected (ChipMEM EDA-PASS-only gate)
        with self.assertRaises(PromotionError):
            store.distill_procedural_skill(
                "proj_soc",
                "Bad unverified skill",
                do_rules=["Gate soft_clr with !m_valid"],
                avoid_rules=["Clear pointers during stall"],
                applies_when="AXI-Stream FIFO has soft_clr and backpressure",
                provenance=prov,
                eda_verification={"tool": "vcs", "passed": False},
            )

        # 2) Unrecognized / LLM self-judged tool MUST be rejected
        with self.assertRaises(PromotionError):
            store.distill_procedural_skill(
                "proj_soc",
                "LLM self-judged skill",
                do_rules=["Trust LLM intuition"],
                avoid_rules=[],
                applies_when="always",
                provenance=prov,
                eda_verification={"tool": "llm_judge", "passed": True},
            )

        # 3) Missing applies_when applicability condition MUST be rejected
        with self.assertRaises(PromotionError):
            store.distill_procedural_skill(
                "proj_soc",
                "Unscoped skill",
                do_rules=["Gate soft_clr"],
                avoid_rules=[],
                applies_when="   ",
                provenance=prov,
                eda_verification={"tool": "vcs", "passed": True},
            )

        # 4) Valid EDA-PASS procedural distillation succeeds and formats DO/AVOID + APPLIES_WHEN
        # Synthetic test record exercises validation; no EDA is run by this test.
        artifact = self._tmp / "eda-result.json"
        artifact.write_text(json.dumps({
            "record_version": "dv-eda-result/v1", "project_id": "proj_soc", "tool": "vcs",
            "check_name": "AST_HOLD_ON_STALL", "passed": True, "returncode": 0, "timed_out": False,
            "execution_mode": "real", "revision": "@CL48210",
            "source_hashes": {"fixtures/rtl/axi_fifo.sv": hashlib.sha256((self._tmp / "fixtures/rtl/axi_fifo.sv").read_bytes()).hexdigest()},
        }), encoding="utf-8")
        eid = store.distill_procedural_skill(
            "proj_soc",
            "AXI-Stream soft_clr stall protection",
            do_rules=["Gate soft_clr pointer reset until !(m_valid && !m_ready)"],
            avoid_rules=["Unconditionally clearing wr_ptr_q/rd_ptr_q during output stall"],
            applies_when="DUT is an AXI-Stream FIFO with synchronous soft_clr",
            provenance=prov,
            eda_verification={"tool": "vcs", "passed": True, "check_name": "AST_HOLD_ON_STALL",
                              "artifact": "eda-result.json", "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()},
            tags=["axi_stream", "soft_clr"],
        )
        store.verify_entry(eid, "proj_soc")
        grant_memory(self, store, eid, "proj_soc", "human:dv_lead", "APR-CM-1")
        entry = store.promote_to_confirmed(eid, "proj_soc", approver="human:dv_lead", approval_id="APR-CM-1")
        self.assertIn("## DO", entry["content"])
        self.assertIn("## AVOID", entry["content"])
        self.assertIn("[iff DUT is an AXI-Stream FIFO", entry["content"])
        self.assertEqual(entry["verification_proof"]["tool"], "vcs")
        self.assertTrue(entry["verification_proof"]["passed"])

        # 5) Frozen mode allows retrieval with similarity gating, blocks mutations
        frozen = MemoryStore(self.mem_db, self._tmp, mode="frozen")
        matched = frozen.retrieve("proj_soc", "axi_stream soft_clr backpressure stall", min_similarity=0.15)
        self.assertEqual(len(matched), 1)
        self.assertGreaterEqual(matched[0]["similarity"], 0.15)

        dissimilar = frozen.retrieve("proj_soc", "ddr5 phy calibration training", min_similarity=0.15)
        self.assertEqual(dissimilar, [])

        with self.assertRaises(PromotionError):
            frozen.add_candidate("proj_soc", "semantic", "Blocked", "Cannot write in frozen mode", prov)

    def test_02_statistical_bayesian_retry_predictor_and_recovery_advisor(self) -> None:
        self.assertEqual(classify_eda_error("vcs", 1, stderr="Error-[XMRE] Cross-module reference"), "vcs_compile_undef")
        self.assertEqual(classify_eda_error("vcs", 1, stdout="UVM_FATAL @ 1260ns"), "uvm_fatal")
        self.assertEqual(classify_eda_error("vcs", 124, timed_out=True), "timeout")
        self.assertEqual(classify_eda_error("vcs", 1, stderr="FlexLM license checkout failed"), "license_unavailable")

        sm = StatisticalToolMemory(self.stat_db, shrinkage_k=8.0, switch_threshold=0.20)

        # Initial Beta(1,1) prior without data is 0.50 -> do not switch yet
        init_pred = sm.predict_retry_success("vcs:vcs", "vcs_compile_undef", retry_count=1)
        self.assertAlmostEqual(init_pred["p_retry"], 0.5, places=3)
        self.assertFalse(init_pred["should_switch_strategy"])

        # Repeated blind retry failures drive posterior p3 below threshold 0.20
        for _ in range(12):
            sm.record_retry_outcome("vcs:vcs", "vcs_compile_undef", retry_count=2, succeeded=False, stage="vcs")
        low_pred = sm.predict_retry_success("vcs:vcs", "vcs_compile_undef", retry_count=2)
        self.assertLess(low_pred["p_retry"], 0.20)
        self.assertTrue(low_pred["should_switch_strategy"])

        # Record recovery outcomes so Recovery Strategy Advisor ranks proven strategy #1
        for _ in range(6):
            sm.record_recovery_outcome("parse_filelist_and_incdir_order", "vcs_compile_undef", True, stage="vcs")
        for _ in range(6):
            sm.record_recovery_outcome("inspect_sv_package_imports", "vcs_compile_undef", False, stage="vcs")

        recs = sm.recommend_recoveries("vcs_compile_undef", stage="vcs", top_k=3)
        self.assertEqual(recs[0]["action"], "parse_filelist_and_incdir_order")
        self.assertGreater(recs[0]["q_success"], recs[-1]["q_success"])

    def test_03_adapter_integration_records_failures_and_emits_recovery_advisory(self) -> None:
        approval_file = self._tmp / "approvals.json"
        approval_file.write_text(
            json.dumps({
                "approvals": [{
                    "approval_id": "APR-VCS-1",
                    "category": "simulation",
                    "approver": "human:dv_lead",
                }]
            }),
            encoding="utf-8",
        )
        cfg = FrameworkConfig.load(
            project_root=self._tmp,
            overrides={
                "dry_run": False,
                "adapter_mode": "real",
                "adapters_validated": {"vcs": True},
            },
        )
        sm = StatisticalToolMemory(self.stat_db, shrinkage_k=4.0, switch_threshold=0.25)
        for _ in range(8):
            sm.record_retry_outcome("vcs:vcs", "vcs_compile_undef", retry_count=1, succeeded=False, stage="vcs")
            sm.record_retry_outcome("vcs:vcs", "vcs_compile_undef", retry_count=2, succeeded=False, stage="vcs")
        for _ in range(5):
            sm.record_recovery_outcome("parse_filelist_and_incdir_order", "vcs_compile_undef", True, stage="vcs")

        def fake_runner(argv, cwd, timeout_s, env):
            return SubprocessResult(
                returncode=1,
                stdout="",
                stderr="Error-[XMRE] Undefined module reference 'axi_pkg'",
                argv=list(argv),
            )

        gate = ApprovalGate(approver=ApprovalFileApprover(approval_file))
        vcs = VCSAdapter(cfg, approval_gate=gate, subprocess_runner=fake_runner, statistical_memory=sm)
        res = vcs.compile(build_target="simv", approval_id="APR-VCS-1")
        self.assertEqual(res["status"], "failed")
        self.assertIn("advisory", res)
        self.assertEqual(res["advisory"]["err_class"], "vcs_compile_undef")
        self.assertTrue(res["advisory"]["should_switch_strategy"])
        self.assertEqual(res["advisory"]["recommended_recoveries"][0]["action"], "parse_filelist_and_incdir_order")


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""R2 unit tests: real-mode VCS / Verdi / Perforce CLI adapters, offline test doubles,
redaction, truncation, path containment, and unconditional Perforce destructive bans.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dv_agent_platform.adapters import PerforceAdapter, SubprocessResult, VCSAdapter, VerdiAdapter  # noqa: E402
from dv_agent_platform.config import FrameworkConfig  # noqa: E402
from dv_agent_platform.errors import AdapterNotValidated, ApprovalRequired, ForbiddenOperation  # noqa: E402
from dv_agent_platform.security.approval import ApprovalFileApprover, ApprovalGate  # noqa: E402


class TestR2RealAdapters(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="dv_r2_test_"))
        shutil.copytree(REPO_ROOT / "fixtures", self._tmp / "fixtures")
        (self._tmp / "sim_out").mkdir()
        (self._tmp / "sim_out" / "wave.fsdb").write_text("SYNTHETIC_FSDB\n", encoding="utf-8")
        appr_file = self._tmp / "approvals.json"
        appr_file.write_text(
            json.dumps({
                "approvals": [
                    {"approval_id": "APR-SIM", "category": "simulation", "approver": "human:dv_lead"},
                    {"approval_id": "APR-WAVE", "category": "waveform_extraction", "approver": "human:dv_lead"},
                    {"approval_id": "APR-P4", "category": "perforce_prepare", "approver": "human:dv_lead"},
                ]
            }),
            encoding="utf-8",
        )
        self.gate = ApprovalGate(approver=ApprovalFileApprover(appr_file))

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _real_cfg(self, adapters=None) -> FrameworkConfig:
        return FrameworkConfig.load(
            project_root=self._tmp,
            overrides={
                "dry_run": False,
                "adapter_mode": "real",
                "adapters_validated": {"vcs": True, "verdi": True, "perforce": True},
                "adapters": adapters or {
                    "vcs": {"bin": "vcs", "timeout_s": 30, "max_output_bytes": 4096},
                    "verdi": {"bin": "verdi", "fsdbreport_bin": "fsdbreport", "timeout_s": 30},
                    "perforce": {"bin": "p4", "timeout_s": 15},
                },
            },
        )

    def test_01_vcs_real_compile_sim_and_compile_log_parser(self) -> None:
        calls = []

        def runner(argv, cwd, timeout_s, env):
            calls.append(list(argv))
            return SubprocessResult(0, "VCS OK\n", "", list(argv))

        vcs = VCSAdapter(self._real_cfg(), approval_gate=self.gate, subprocess_runner=runner)
        c_res = vcs.compile(build_target="simv", extra_args=["-sverilog"], approval_id="APR-SIM")
        self.assertEqual(c_res["status"], "compiled")
        s_res = vcs.run_simulation("axi_smoke", seed=17, approval_id="APR-SIM")
        self.assertEqual(s_res["status"], "executed")
        self.assertEqual(len(calls), 2)

        clog = self._tmp / "sim_out" / "comp.log"
        clog.write_text("Warning-[TMR] minor\nError-[SE] Syntax error at rtl.sv:12\n", encoding="utf-8")
        parsed = vcs.parse_compile_log("sim_out/comp.log")
        self.assertEqual(parsed["error_count"], 1)
        self.assertEqual(parsed["warning_count"], 1)

    def test_02_verdi_real_extract_and_path_escape_guard(self) -> None:
        def runner(argv, cwd, timeout_s, env):
            return SubprocessResult(0, "time(ns) clk m_valid\n1240 1 1\n1250 1 0\n", "", list(argv))

        verdi = VerdiAdapter(self._real_cfg(), approval_gate=self.gate, subprocess_runner=runner)
        res = verdi.extract_fsdb_slice("sim_out/wave.fsdb", ["clk", "m_valid"], 1240, 1250, approval_id="APR-WAVE")
        self.assertEqual(res["status"], "extracted")
        self.assertEqual(len(res["samples"]), 2)
        self.assertEqual(res["samples"][0]["time_ns"], 1240)

        with self.assertRaises(ValueError):
            verdi.extract_fsdb_slice("../outside.fsdb", ["clk"], 0, 10, approval_id="APR-WAVE")

    def test_03_perforce_real_ztag_and_unconditional_blocks(self) -> None:
        calls = []

        def runner(argv, cwd, timeout_s, env):
            calls.append(list(argv))
            if "info" in argv:
                return SubprocessResult(0, "... userName dv_eng\n... clientName ws_soc\n", "", list(argv))
            if "changes" in argv:
                return SubprocessResult(0, "... change 48210\n... user dv_eng\n... desc Fix stall\n", "", list(argv))
            if "describe" in argv:
                return SubprocessResult(0, "... change 48210\n... desc Fix stall\n", "", list(argv))
            if "filelog" in argv:
                return SubprocessResult(0, "... depotFile //depot/axi_fifo.sv\n... rev0 3\n", "", list(argv))
            if "annotate" in argv:
                return SubprocessResult(0, "... data 40: if (soft_clr) begin\n", "", list(argv))
            return SubprocessResult(0, "ok\n", "", list(argv))

        p4 = PerforceAdapter(self._real_cfg(), approval_gate=self.gate, subprocess_runner=runner)
        self.assertEqual(p4.status()["client"], "ws_soc")
        self.assertEqual(p4.changes(limit=5)[0]["cl"], 48210)
        self.assertEqual(p4.describe(48210)["cl"], 48210)
        self.assertEqual(p4.diff(["fixtures/rtl/axi_fifo.sv"])["mode"], "real")
        self.assertEqual(len(p4.filelog("fixtures/rtl/axi_fifo.sv")), 1)
        self.assertEqual(p4.annotate("fixtures/rtl/axi_fifo.sv")["mode"], "real")
        edit = p4.prepare_edit(["fixtures/rtl/axi_fifo.sv"], "prep", approval_id="APR-P4")
        self.assertEqual(edit["status"], "opened")
        self.assertFalse(edit["submit_allowed"])

        before = len(calls)
        for fn in (
            lambda: p4.submit(48210, approval_id="APR-P4"),
            lambda: p4.push(),
            lambda: p4.obliterate("//depot/x"),
            lambda: p4.revert(["fixtures/rtl/axi_fifo.sv"]),
            lambda: p4._run_command(["p4", "change", "-d", "48210"]),
        ):
            with self.assertRaises(ForbiddenOperation):
                fn()
        self.assertEqual(len(calls), before)

    def test_04_redaction_truncation_injection_and_approval_guards(self) -> None:
        fake_gh = "ghp_" + ("9" * 34)

        def runner(argv, cwd, timeout_s, env):
            return SubprocessResult(0, f"tok={fake_gh} " + ("Z" * 500), f"err={fake_gh}", list(argv))

        cfg = self._real_cfg(adapters={"vcs": {"bin": "vcs", "timeout_s": 5, "max_output_bytes": 140}})
        vcs = VCSAdapter(cfg, subprocess_runner=runner)
        res = vcs._run_command(["vcs", "-version"])
        self.assertNotIn(fake_gh, res.stdout)
        self.assertNotIn(fake_gh, res.stderr)
        self.assertIn("[REDACTED]", res.stdout)
        self.assertTrue(res.truncated)
        self.assertLessEqual(len(res.stdout.encode("utf-8")), 140)

        with self.assertRaises(ForbiddenOperation):
            vcs._run_command(["vcs", "; rm -rf /"])
        with self.assertRaises(ForbiddenOperation):
            vcs._run_command(["sh", "-c", "p4 submit"])
        with self.assertRaises(ApprovalRequired):
            vcs.compile()

        unval = FrameworkConfig.load(project_root=self._tmp, overrides={"adapter_mode": "real"})
        with self.assertRaises(AdapterNotValidated):
            PerforceAdapter(unval).status()


if __name__ == "__main__":
    unittest.main(verbosity=2)

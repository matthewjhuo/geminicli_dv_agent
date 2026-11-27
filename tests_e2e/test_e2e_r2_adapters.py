"""E2E Suite — Area A2: R2 Real VCS / Verdi / Perforce Adapters & MCP Wiring.

Covers PROJECT.md F2.1–F2.5 and ORIGINAL_REQUEST.md R2:
- Tier 1 (>= 6): Real-mode VCS compile/sim & log parsing via injectable subprocess_runner,
  Verdi suggest_window & real extract_fsdb_slice, Perforce real -ztag read-only
  (status/changes/describe/diff/filelog/annotate) & approval-gated prepare_edit,
  MCP dv-vcs/dv-verdi/dv-p4 stdio tools + <untrusted_dv_data> fencing.
- Tier 2 (>= 6): AdapterNotValidated guard, unconditional ForbiddenOperation on Perforce
  submit/push/obliterate/revert & destructive flags, ApprovalRequired vs dry_run,
  subprocess timeout + output truncation + secret redaction, path traversal rejection,
  command injection blocking in _run_command.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from tests_e2e.e2e_helpers import (
    copy_fixtures,
    dyn_fake_secret,
    dyn_secret_kv,
    resolve_symbol,
    write_approval_file,
)

from dv_agent_platform.adapters import PerforceAdapter, VCSAdapter, VerdiAdapter
from dv_agent_platform.config import FrameworkConfig
from dv_agent_platform.errors import AdapterNotValidated, ApprovalRequired, ForbiddenOperation
from dv_agent_platform.mcp_servers import create_dv_mcp_server
from dv_agent_platform.security.approval import ApprovalFileApprover, ApprovalGate


class _RecordingRunner:
    """Deterministic offline subprocess_runner double recording all argv calls."""

    def __init__(self, handler=None) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._handler = handler

    def __call__(
        self,
        argv: Sequence[str],
        cwd: Optional[Path] = None,
        timeout_s: int = 60,
        env: Optional[Dict[str, str]] = None,
    ):
        SubprocessResult = resolve_symbol([
            ("dv_agent_platform.adapters.base", "SubprocessResult"),
            ("dv_agent_platform.adapters", "SubprocessResult"),
        ])
        self.calls.append({"argv": list(argv), "cwd": cwd, "timeout_s": timeout_s, "env": env})
        if self._handler:
            return self._handler(list(argv), cwd, timeout_s, env, SubprocessResult)
        return SubprocessResult(returncode=0, stdout="", stderr="", argv=list(argv), timed_out=False)


class TestE2ER2Adapters(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_ctx = tempfile.TemporaryDirectory(prefix="e2e_r2_")
        self.tmp = Path(self._tmp_ctx.name)
        copy_fixtures(self.tmp)
        (self.tmp / "sim_out").mkdir(exist_ok=True)
        (self.tmp / "sim_out" / "wave.fsdb").write_text("SYNTHETIC_FSDB_PLACEHOLDER\n", encoding="utf-8")

        self.approval_file = write_approval_file(
            self.tmp / "approvals.json",
            [
                {"approval_id": "APR-SIM-1", "category": "simulation", "approver": "human:dv_lead"},
                {"approval_id": "APR-WAVE-1", "category": "waveform_extraction", "approver": "human:dv_lead"},
                {"approval_id": "APR-P4-1", "category": "perforce_prepare", "approver": "human:dv_lead"},
            ],
        )
        self.gate = ApprovalGate(approver=ApprovalFileApprover(self.approval_file))

    def tearDown(self) -> None:
        self._tmp_ctx.cleanup()

    def _real_cfg(self, validated: Optional[Dict[str, bool]] = None, adapters: Optional[Dict[str, Any]] = None) -> FrameworkConfig:
        return FrameworkConfig.load(
            project_root=self.tmp,
            overrides={
                "dry_run": False,
                "adapter_mode": "real",
                "adapters_validated": validated if validated is not None else {"vcs": True, "verdi": True, "perforce": True},
                "adapters": adapters or {
                    "vcs": {"bin": "vcs", "timeout_s": 30, "max_output_bytes": 4096},
                    "verdi": {"bin": "verdi", "fsdbreport_bin": "fsdbreport", "timeout_s": 30},
                    "perforce": {"bin": "p4", "timeout_s": 15},
                },
            },
        )

    # ------------------------------------------------------------------
    # Tier 1: Nominal / Happy Path (>= 6)
    # ------------------------------------------------------------------
    def test_t1_01_vcs_real_compile_and_run_simulation_via_subprocess_runner(self) -> None:
        def _handler(argv, cwd, timeout_s, env, ResultCls):
            joined = " ".join(argv)
            if "vcs" in argv[0] or "compile" in joined:
                return ResultCls(0, "Chronologic VCS simulator copyright\nTop Level Modules:\n  axi_fifo\n", "", argv, False)
            return ResultCls(0, "UVM_INFO @ 100 ns: TEST PASSED\n", "", argv, False)

        runner = _RecordingRunner(_handler)
        cfg = self._real_cfg()
        vcs = VCSAdapter(cfg, approval_gate=self.gate, subprocess_runner=runner)

        comp = vcs.compile(build_target="simv", extra_args=["-sverilog"], approved=True, approval_id="APR-SIM-1")
        self.assertIsInstance(comp, dict)
        self.assertGreaterEqual(len(runner.calls), 1)
        self.assertIsInstance(runner.calls[0]["argv"], list)

        sim = vcs.run_simulation("axi_fifo_smoke", seed=77, dry_run=False, approval_id="APR-SIM-1")
        self.assertEqual(sim["approved_by"], "human:dv_lead")
        self.assertEqual(sim["status"], "executed")
        self.assertGreaterEqual(len(runner.calls), 2)

    def test_t1_02_vcs_parse_compile_log_and_parse_log(self) -> None:
        compile_log = self.tmp / "sim_out" / "compile.log"
        compile_log.write_text(
            "\n".join([
                "Warning-[TMR] Timescale missing on module foo",
                "Error-[SE] Syntax error",
                "  fixtures/rtl/axi_fifo.sv, 42",
                "  \"if (soft_clr) begin\"",
                "1 error",
            ]),
            encoding="utf-8",
        )
        cfg = FrameworkConfig.load(project_root=self.tmp, overrides={"dry_run": False, "adapter_mode": "mock"})
        vcs = VCSAdapter(cfg)

        comp_parsed = vcs.parse_compile_log("sim_out/compile.log")
        self.assertIsInstance(comp_parsed, dict)
        self.assertGreaterEqual(comp_parsed.get("error_count", 0), 1)

        sim_parsed = vcs.parse_log("fixtures/logs/simv_fail_seed1042.log")
        self.assertEqual(sim_parsed["test"], "axi_fifo_soft_clr_stress_test")
        self.assertEqual(sim_parsed["seed"], "1042")
        self.assertGreaterEqual(sim_parsed["error_count"], 1)
        self.assertIsNotNone(sim_parsed["first_failure"])

    def test_t1_03_verdi_suggest_window_and_real_extract_fsdb_slice(self) -> None:
        def _handler(argv, cwd, timeout_s, env, ResultCls):
            stdout = (
                "time(ns) clk rst_n m_valid m_ready\n"
                "1240     1   1     1       0\n"
                "1250     1   1     1       0\n"
                "1260     1   1     0       0\n"
            )
            return ResultCls(0, stdout, "", argv, False)

        runner = _RecordingRunner(_handler)
        cfg = self._real_cfg()
        verdi = VerdiAdapter(cfg, approval_gate=self.gate, subprocess_runner=runner)

        win = verdi.suggest_window("tb_top.u_dut", 1260)
        self.assertEqual(win["window_ns"], [1210, 1280])
        self.assertTrue(len(win["recommended_signals"]) >= 1)

        slc = verdi.extract_fsdb_slice(
            "sim_out/wave.fsdb",
            ["clk", "rst_n", "m_valid", "m_ready"],
            1240,
            1260,
            dry_run=False,
            approval_id="APR-WAVE-1",
        )
        self.assertEqual(slc["status"], "extracted")
        self.assertEqual(slc["approved_by"], "human:dv_lead")
        self.assertGreaterEqual(len(slc["samples"]), 1)
        self.assertGreaterEqual(len(runner.calls), 1)

    def test_t1_04_perforce_real_readonly_ztag_commands(self) -> None:
        def _handler(argv, cwd, timeout_s, env, ResultCls):
            joined = " ".join(argv)
            if "info" in joined or "opened" in joined:
                out = "... userName dv_eng\n... clientName ws_soc\n"
            elif "changes" in joined:
                out = (
                    "... change 48210\n... user dv_eng\n... status submitted\n... desc Fix AXI FIFO stall\n\n"
                    "... change 48195\n... user dv_eng\n... status submitted\n... desc Add SVA\n\n"
                )
            elif "describe" in joined:
                out = "... change 48210\n... user dv_eng\n... depotFile0 //depot/soc/axi_fifo.sv\n... desc Fix AXI FIFO stall\n"
            elif "filelog" in joined:
                out = "... depotFile //depot/soc/axi_fifo.sv\n... rev0 12\n... change0 48210\n"
            elif "annotate" in joined:
                out = "... data 40: if (soft_clr) begin\n"
            elif "diff" in joined:
                out = "==== //depot/soc/axi_fifo.sv#12 - fixtures/rtl/axi_fifo.sv ====\n@@ -40,1 +40,1 @@\n"
            else:
                out = ""
            return ResultCls(0, out, "", argv, False)

        runner = _RecordingRunner(_handler)
        cfg = self._real_cfg()
        p4 = PerforceAdapter(cfg, approval_gate=self.gate, subprocess_runner=runner)

        st = p4.status()
        self.assertIsInstance(st, dict)
        chs = p4.changes(limit=5)
        self.assertGreaterEqual(len(chs), 1)
        desc = p4.describe(48210)
        self.assertEqual(int(desc.get("cl", desc.get("change", 48210))), 48210)
        diff_res = p4.diff(["fixtures/rtl/axi_fifo.sv"])
        self.assertIsInstance(diff_res, (dict, list, str))
        flog = p4.filelog("fixtures/rtl/axi_fifo.sv", limit=5)
        self.assertIsInstance(flog, (dict, list))
        ann = p4.annotate("fixtures/rtl/axi_fifo.sv")
        self.assertIsInstance(ann, (dict, list, str))
        self.assertTrue(any("-ztag" in call["argv"] for call in runner.calls))

    def test_t1_05_perforce_prepare_edit_approval_gated_never_submits(self) -> None:
        runner = _RecordingRunner()
        cfg = self._real_cfg()
        p4 = PerforceAdapter(cfg, approval_gate=self.gate, subprocess_runner=runner)

        res = p4.prepare_edit(
            ["fixtures/rtl/axi_fifo.sv"],
            "Prepare RTL fix for review",
            dry_run=False,
            approval_id="APR-P4-1",
        )
        self.assertEqual(res["approved_by"], "human:dv_lead")
        self.assertIs(res["submit_allowed"], False)
        for call in runner.calls:
            self.assertNotIn("submit", call["argv"])

    def test_t1_06_mcp_servers_expose_safe_tools_with_untrusted_fencing(self) -> None:
        cfg = FrameworkConfig.load(project_root=self.tmp, overrides={"dry_run": False, "adapter_mode": "mock"})
        for profile in ("dv-vcs", "dv-verdi", "dv-p4"):
            srv = create_dv_mcp_server(profile, cfg, approval_gate=self.gate)
            tool_names = {t["name"] for t in srv.list_tools()}
            self.assertGreaterEqual(len(tool_names), 2)
            if profile == "dv-p4":
                self.assertNotIn("submit", tool_names)
                self.assertNotIn("obliterate", tool_names)

        p4_srv = create_dv_mcp_server("dv-p4", cfg, approval_gate=self.gate)
        req = io.StringIO(
            "\n".join([
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
                json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "describe", "arguments": {"changelist": 48210}}}),
            ]) + "\n"
        )
        out = io.StringIO()
        p4_srv.serve_stdio(req, out)
        responses = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(responses), 3)
        body = responses[2]["result"]["content"][0]["text"]
        self.assertIn("<untrusted_dv_data", body)
        self.assertIn("48210", body)

    # ------------------------------------------------------------------
    # Tier 2: Boundaries, Blocks, Timeouts, Redaction & Containment (>= 6)
    # ------------------------------------------------------------------
    def test_t2_01_real_mode_without_validation_raises_adapter_not_validated(self) -> None:
        unval_cfg = self._real_cfg(validated={"vcs": False, "verdi": False, "perforce": False})
        with self.assertRaises(AdapterNotValidated):
            VCSAdapter(unval_cfg).parse_log("fixtures/logs/simv_fail_seed1042.log")
        with self.assertRaises(AdapterNotValidated):
            VerdiAdapter(unval_cfg).suggest_window("tb_top.u_dut", 1260)
        with self.assertRaises(AdapterNotValidated):
            PerforceAdapter(unval_cfg).status()

    def test_t2_02_perforce_forbidden_operations_and_flags_blocked(self) -> None:
        runner = _RecordingRunner()
        cfg = self._real_cfg()
        p4 = PerforceAdapter(cfg, approval_gate=self.gate, subprocess_runner=runner)

        with self.assertRaises(ForbiddenOperation):
            p4.submit(changelist=48210, approval_id="APR-P4-1")
        with self.assertRaises(ForbiddenOperation):
            p4.push()
        with self.assertRaises(ForbiddenOperation):
            p4.obliterate("//depot/soc/axi_fifo.sv")
        with self.assertRaises(ForbiddenOperation):
            p4.revert(["fixtures/rtl/axi_fifo.sv"])
        with self.assertRaises(ForbiddenOperation):
            p4._run_command(["p4", "change", "-d", "48210"])
        self.assertEqual(len(runner.calls), 0, "Forbidden p4 commands must never reach subprocess_runner")

    def test_t2_03_approval_required_blocks_unapproved_and_dry_run_skips_exec(self) -> None:
        runner = _RecordingRunner()
        cfg = self._real_cfg()
        vcs = VCSAdapter(cfg, subprocess_runner=runner)  # default DenyAllApprover
        verdi = VerdiAdapter(cfg, subprocess_runner=runner)
        p4 = PerforceAdapter(cfg, subprocess_runner=runner)

        with self.assertRaises(ApprovalRequired):
            vcs.compile()
        with self.assertRaises(ApprovalRequired):
            vcs.run_simulation("axi_fifo_smoke", seed=1)
        with self.assertRaises(ApprovalRequired):
            verdi.extract_fsdb_slice("sim_out/wave.fsdb", ["clk"], 0, 100)
        with self.assertRaises(ApprovalRequired):
            p4.prepare_edit(["fixtures/rtl/axi_fifo.sv"], "edit")
        self.assertEqual(len(runner.calls), 0)

        dry_sim = vcs.run_simulation("axi_fifo_smoke", seed=1, dry_run=True)
        dry_wave = verdi.extract_fsdb_slice("sim_out/wave.fsdb", ["clk"], 0, 100, dry_run=True)
        dry_p4 = p4.prepare_edit(["fixtures/rtl/axi_fifo.sv"], "edit", dry_run=True)
        self.assertEqual(dry_sim["status"], "dry_run")
        self.assertEqual(dry_wave["status"], "dry_run")
        self.assertEqual(dry_p4["status"], "dry_run")
        self.assertEqual(len(runner.calls), 0)

    def test_t2_04_subprocess_timeout_truncation_and_secret_redaction(self) -> None:
        secret_tok = dyn_fake_secret("ghp")
        s_key, s_val = dyn_secret_kv("p4_auth")

        def _noisy_handler(argv, cwd, timeout_s, env, ResultCls):
            if "timeout_probe" in " ".join(argv):
                return ResultCls(returncode=124, stdout="", stderr="timed out", argv=argv, timed_out=True)
            big_out = f"line_start token={secret_tok} {s_key}={s_val} " + ("X" * 800)
            return ResultCls(returncode=0, stdout=big_out, stderr=f"err={secret_tok}", argv=argv, timed_out=False)

        runner = _RecordingRunner(_noisy_handler)
        cfg = self._real_cfg(adapters={"vcs": {"bin": "vcs", "timeout_s": 5, "max_output_bytes": 160}})
        vcs = VCSAdapter(cfg, approval_gate=self.gate, subprocess_runner=runner)

        res = vcs._run_command(["vcs", "-version"])
        self.assertNotIn(secret_tok, res.stdout)
        self.assertNotIn(s_val, res.stdout)
        self.assertNotIn(secret_tok, res.stderr)
        self.assertIn("[REDACTED]", res.stdout)
        self.assertLessEqual(len(res.stdout.encode("utf-8")), 256)

        t_res = vcs._run_command(["vcs", "timeout_probe"])
        self.assertTrue(t_res.timed_out)

    def test_t2_05_path_traversal_outside_project_root_rejected(self) -> None:
        cfg = FrameworkConfig.load(project_root=self.tmp, overrides={"dry_run": False, "adapter_mode": "mock"})
        vcs = VCSAdapter(cfg, approval_gate=self.gate)
        verdi = VerdiAdapter(cfg, approval_gate=self.gate)
        p4 = PerforceAdapter(cfg, approval_gate=self.gate)

        with self.assertRaises(ValueError):
            vcs.parse_log("../../etc/passwd")
        with self.assertRaises(ValueError):
            vcs.parse_compile_log("../outside_compile.log")
        with self.assertRaises(ValueError):
            verdi.extract_fsdb_slice("../outside.fsdb", ["clk"], 0, 10, dry_run=False, approval_id="APR-WAVE-1")
        with self.assertRaises(ValueError):
            p4.prepare_edit(["../escape.sv"], "bad", dry_run=False, approval_id="APR-P4-1")

    def test_t2_06_command_injection_in_adapter_args_blocked(self) -> None:
        runner = _RecordingRunner()
        cfg = self._real_cfg()
        vcs = VCSAdapter(cfg, approval_gate=self.gate, subprocess_runner=runner)

        with self.assertRaises(ForbiddenOperation):
            vcs._run_command(["vcs", "; rm -rf /"])
        with self.assertRaises(ForbiddenOperation):
            vcs._run_command(["sh", "-c", "p4 submit -d auto"])
        with self.assertRaises((ForbiddenOperation, ValueError)):
            vcs._run_command([])
        self.assertEqual(len(runner.calls), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

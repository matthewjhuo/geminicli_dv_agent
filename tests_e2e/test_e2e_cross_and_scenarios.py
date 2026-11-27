"""E2E Suite — Tier 3 Pairwise Cross-Feature & Tier 4 Realistic DV Scenarios.

Covers:
- Tier 3 (>= 6): Pairwise combinations A1<->A2, A1<->A5, A2<->A4, A2<->A5, A3<->A5, A4<->A5.
- Tier 4 (>= 5): S1 Company TB onboarding + dry-run + rollback; S2 Multi-log regression
  triage -> scan_sim_out -> Verdi window/slice -> MD report; S3 rtl-trace / .f inspection
  -> injection quarantine -> memory candidate -> human promote -> staleness; S4 Perforce
  read-only + prepare_edit + submit defense-in-depth; S5 Harness^2 safety gate + dry_sim
  + gemini_cli shim eval + scaffold hygiene.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path

from tests_e2e.e2e_helpers import (
    HOOKS_DIR,
    POLICIES_DIR,
    REPO_ROOT,
    SCRIPTS_DIR,
    copy_fixtures,
    dyn_fake_secret,
    ensure_harness2_for_test,
    make_agent_response,
    resolve_symbol,
    run_cli,
    run_hook,
    run_script,
    write_approval_file,
    write_fake_cli,
)

from dv_agent_platform.adapters import PerforceAdapter, RegressionAdapter, VCSAdapter, VerdiAdapter
from dv_agent_platform.config import FrameworkConfig
from dv_agent_platform.errors import ForbiddenOperation
from dv_agent_platform.harness2_bridge import DVHarness2Adapter, DVOfflineProposer, validate_dv_harness_safety
from dv_agent_platform.mcp_servers import create_dv_mcp_server
from dv_agent_platform.memory.store import MemoryStore
from dv_agent_platform.orchestrator.backends import GeminiCliBackend, MockBackend
from dv_agent_platform.orchestrator.runner import Orchestrator
from dv_agent_platform.security.approval import ApprovalFileApprover, ApprovalGate
from dv_agent_platform.security.command_guard import classify_command
from dv_agent_platform.security.untrusted import detect_injection


def _company_profile() -> dict:
    return {
        "profile_version": "1.0",
        "project": {
            "project_id": "soc_axi_dma_dv",
            "dut_name": "axi_dma_top",
            "adapter_mode": "mock",
            "dry_run": True,
        },
        "paths": {
            "rtl_root": "fixtures/rtl",
            "tb_root": "fixtures/verif",
            "uvm_env_root": "fixtures/verif",
            "filelist": "top.f",
        },
        "testbench": {
            "dut_name": "axi_dma_top",
            "clocks": ["aclk"],
            "resets": ["aresetn"],
            "interfaces": ["axi4_m", "axi4_s"],
            "compile_cmd": "vcs -sverilog -f ${DV_PROJECT_ROOT}/top.f",
            "sim_cmd": "./simv +UVM_TESTNAME=${TEST} +ntb_random_seed=${SEED}",
            "regression_cmd": "make regress",
            "verdi_cmd": "verdi -ssf sim_out/wave.fsdb",
        },
        "adapters": {
            "vcs": {"bin": "vcs", "timeout_s": 60, "max_output_bytes": 8192},
            "verdi": {"bin": "verdi", "fsdbreport_bin": "fsdbreport", "timeout_s": 60},
            "perforce": {"bin": "p4", "timeout_s": 30},
        },
        "runtime": {
            "python_bin": "python",
        },
    }


import sys as _test_sys
_test_sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from approval_helpers import grant_memory


class TestE2ECrossAndScenarios(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_ctx = tempfile.TemporaryDirectory(prefix="e2e_x_")
        self.tmp = Path(self._tmp_ctx.name)

    def tearDown(self) -> None:
        self._tmp_ctx.cleanup()

    # ------------------------------------------------------------------
    # Tier 3: Pairwise Cross-Feature Combinations (>= 6)
    # ------------------------------------------------------------------
    def test_t3_01_a1_a2_profile_applied_config_drives_real_vcs_adapter(self) -> None:
        ws = self.tmp / "t3_a1_a2"
        ws.mkdir()
        copy_fixtures(ws)
        prof = _company_profile()
        prof["project"]["adapter_mode"] = "real"
        prof_path = self.tmp / "prof.json"
        prof_path.write_text(json.dumps(prof), encoding="utf-8")

        p = run_script(
            SCRIPTS_DIR / "install.py",
            ["--project-dir", str(ws), "--profile", str(prof_path), "--apply", "--strict-render"],
        )
        self.assertEqual(p.returncode, 0, p.stderr)

        SubprocessResult = resolve_symbol([
            ("dv_agent_platform.adapters.base", "SubprocessResult"),
            ("dv_agent_platform.adapters", "SubprocessResult"),
        ])
        app_file = write_approval_file(
            self.tmp / "apr_t3_1.json",
            [{"approval_id": "APR-1", "category": "simulation", "approver": "human:dv_lead"}],
        )
        cfg = FrameworkConfig.load(project_root=ws, overrides={"dry_run": False, "adapters_validated": {"vcs": True}})
        recorded = []

        def runner(argv, cwd=None, timeout_s=30, env=None):
            recorded.append(list(argv))
            return SubprocessResult(0, "UVM_INFO @ 10 ns: PASSED", "", list(argv), False)

        vcs = VCSAdapter(cfg, approval_gate=ApprovalGate(ApprovalFileApprover(app_file)), subprocess_runner=runner)
        res = vcs.run_simulation("axi_fifo_smoke", seed=9, dry_run=False, approval_id="APR-1")
        self.assertEqual(res["status"], "executed")
        self.assertEqual(len(recorded), 1)

    def test_t3_02_a1_a5_rendered_template_passes_safety_and_hook_guards(self) -> None:
        ws = self.tmp / "t3_a1_a5"
        ws.mkdir()
        prof_path = self.tmp / "prof_a1_a5.json"
        prof_path.write_text(json.dumps(_company_profile()), encoding="utf-8")

        p = run_script(
            SCRIPTS_DIR / "install.py",
            ["--project-dir", str(ws), "--profile", str(prof_path), "--apply", "--strict-render"],
        )
        self.assertEqual(p.returncode, 0, p.stderr)

        # Verify installed BeforeTool hook inside rendered workspace denies p4.exe submit
        hook = ws / ".gemini" / "hooks" / "before_tool_guard.py"
        self.assertTrue(hook.is_file())
        hp = run_hook(
            hook,
            {"tool_name": "run_shell_command", "tool_input": {"command": "p4.exe submit"}, "cwd": str(ws)},
            cwd=ws,
        )
        self.assertEqual(json.loads(hp.stdout)["decision"], "deny")

    def test_t3_03_a2_a4_scan_sim_out_feeds_rtl_trace_and_markdown_report(self) -> None:
        ws = self.tmp / "t3_a2_a4"
        ws.mkdir()
        copy_fixtures(ws)
        sim_out = ws / "sim_out"
        sim_out.mkdir()
        (sim_out / "simv_a.log").write_text(
            "+UVM_TESTNAME=axi_fifo_soft_clr_stress_test +ntb_random_seed=101\n"
            "UVM_ERROR tb.sv(21) @ 100 ns: [SB_MISMATCH] Exp=0x11 Act=0x22\n",
            encoding="utf-8",
        )
        (sim_out / "simv_b.log").write_text(
            "+UVM_TESTNAME=axi_fifo_soft_clr_stress_test +ntb_random_seed=102\n"
            "UVM_ERROR tb.sv(21) @ 200 ns: [SB_MISMATCH] Exp=0x33 Act=0x44\n",
            encoding="utf-8",
        )
        cfg = FrameworkConfig.load(project_root=ws, overrides={"dry_run": False, "adapter_mode": "mock"})
        clusters = RegressionAdapter(cfg).scan_sim_out("sim_out")
        self.assertEqual(clusters["total_failures"], 2)
        self.assertEqual(len(clusters["buckets"]), 1)

        from dv_agent_platform.orchestrator.workflows import BUILTIN_WORKFLOWS
        spec = BUILTIN_WORKFLOWS["rtl-trace"]
        responses = {
            t["id"]: make_agent_response(
                t["agent"], t["id"], "RTL_DEFECT",
                review_verdict="approve" if t["agent"] == "dv-review" else None,
            )
            for t in spec["tasks"]
            if t.get("kind") != "aggregate"
        }
        state = Orchestrator(cfg, backend=MockBackend(responses)).run_workflow("rtl-trace", params={"rtl_root": "fixtures/rtl"})
        render_run_report = resolve_symbol([
            ("dv_agent_platform.orchestrator.reports", "render_run_report"),
            ("dv_agent_platform.orchestrator.runner", "render_run_report"),
        ])
        md = render_run_report(state)
        self.assertIn("rtl-trace", md)
        self.assertIn(state["run_id"], md)

    def test_t3_04_a2_a5_perforce_and_mcp_and_guard_defense_in_depth(self) -> None:
        ws = self.tmp / "t3_a2_a5"
        ws.mkdir()
        copy_fixtures(ws)
        cfg = FrameworkConfig.load(project_root=ws, overrides={"dry_run": False, "adapter_mode": "mock"})
        p4 = PerforceAdapter(cfg)
        with self.assertRaises(ForbiddenOperation):
            p4.submit()
        srv = create_dv_mcp_server("dv-p4", cfg)
        self.assertNotIn("submit", {t["name"] for t in srv.list_tools()})
        self.assertEqual(classify_command("p4.exe submit").category, "forbidden")
        deny_toml = (POLICIES_DIR / "90-dv-hard-denies.toml").read_text(encoding="utf-8")
        self.assertIn("p4 submit", deny_toml)

    def test_t3_05_a3_a5_harness2_safety_and_isolated_gemini_cli_backend(self) -> None:
        h2_root = ensure_harness2_for_test(self.tmp, self.addCleanup)
        out_root = self.tmp / "t3_a3_a5_out"
        adapter = DVHarness2Adapter(
            framework_root=REPO_ROOT,
            output_root=out_root,
            execution_mode="dry_sim",
            task_filter=["GOLD-SEC-004"],
            harness2_dir=h2_root,
        )
        base_files = {
            p.relative_to(adapter.base_harness()).as_posix(): p.read_text(encoding="utf-8")
            for p in adapter.base_harness().rglob("*")
            if p.is_file()
        }
        validate_dv_harness_safety(base_files)
        # Ensure base_harness cannot pass if $DV_PROJECT_ROOT is stripped
        tampered = dict(base_files)
        tampered["GEMINI.md"] = tampered["GEMINI.md"].replace("$DV_PROJECT_ROOT", "LOCAL_PATH")
        with self.assertRaises(ValueError):
            validate_dv_harness_safety(tampered)
        # Ensure GeminiCliBackend preserves read-only plan mode
        b = GeminiCliBackend(approval_mode="plan")
        self.assertIn("plan", b.build_argv("dv-formal", "Check SVA"))

    def test_t3_06_a4_a5_sv_inspector_injection_triggers_audit_and_memory_gate(self) -> None:
        ws = self.tmp / "t3_a4_a5"
        ws.mkdir()
        copy_fixtures(ws)
        inspect_sv_context = resolve_symbol([
            ("dv_agent_platform.dv_tools", "inspect_sv_context"),
            ("dv_agent_platform.dv_tools.context_inspector", "inspect_sv_context"),
            ("dv_agent_platform.analysis.context_inspector", "inspect_sv_context"),
        ])
        inspected = inspect_sv_context(["fixtures/rtl/axi_fifo.sv"], ws)
        serialized = json.dumps(inspected, default=str)
        self.assertIn("<untrusted_dv_data", serialized)

        # Feed RTL file with embedded injection comment through AfterTool audit hook
        rtl_text = (ws / "fixtures" / "rtl" / "axi_fifo.sv").read_text(encoding="utf-8")
        self.assertGreaterEqual(len(detect_injection(rtl_text)), 1)
        hp = run_hook(
            HOOKS_DIR / "after_tool_audit.py",
            {"tool_name": "read_file", "tool_input": {"path": "fixtures/rtl/axi_fifo.sv"}, "tool_response": rtl_text, "cwd": str(ws)},
            cwd=ws,
        )
        self.assertIn("SECURITY_NOTICE", json.loads(hp.stdout).get("systemMessage", ""))

    # ------------------------------------------------------------------
    # Tier 4: Realistic End-to-End DV Scenarios (>= 5)
    # ------------------------------------------------------------------
    def test_t4_s1_company_tb_onboarding_dry_run_workflow_and_rollback(self) -> None:
        ws = self.tmp / "s1_company_ws"
        ws.mkdir()
        (ws / "GEMINI.md").write_text("# Pre-existing Company Rules\n", encoding="utf-8")
        prof_path = self.tmp / "s1_profile.json"
        prof_path.write_text(json.dumps(_company_profile()), encoding="utf-8")

        # 1. Default dry-run preview makes zero mutations
        p_dry = run_script(SCRIPTS_DIR / "install.py", ["--project-dir", str(ws), "--profile", str(prof_path)])
        self.assertEqual(p_dry.returncode, 0, p_dry.stderr)
        self.assertFalse((ws / "dv_project.json").exists())

        # 2. Apply with strict render + VS Code + env example
        p_apply = run_script(
            SCRIPTS_DIR / "install.py",
            [
                "--project-dir", str(ws),
                "--profile", str(prof_path),
                "--apply", "--strict-render", "--emit-vscode", "--emit-env-example",
            ],
        )
        self.assertEqual(p_apply.returncode, 0, p_apply.stderr)
        backup_dir = json.loads(p_apply.stdout)[0]["backup_dir"]

        # 3. Run CLI workflow dry-run + runs/status inspection
        p_run = run_cli(
            ["--project-root", str(ws), "run", "triage", "-P", "log_path=sim.log", "-P", "test=smoke"],
            cwd=ws,
        )
        self.assertEqual(p_run.returncode, 0, p_run.stderr)
        run_info = json.loads(p_run.stdout)
        self.assertTrue(run_info["dry_run"])

        p_status = run_cli(["--project-root", str(ws), "status", run_info["run_id"]], cwd=ws)
        self.assertEqual(p_status.returncode, 0, p_status.stderr)
        self.assertEqual(json.loads(p_status.stdout)["workflow"], "triage")

        # 4. Rollback restores original GEMINI.md
        p_rb = run_script(SCRIPTS_DIR / "install.py", ["--rollback", backup_dir])
        self.assertEqual(p_rb.returncode, 0, p_rb.stderr)
        self.assertEqual((ws / "GEMINI.md").read_text(encoding="utf-8"), "# Pre-existing Company Rules\n")

    def test_t4_s2_multi_log_regression_triage_to_verdi_and_md_report(self) -> None:
        ws = self.tmp / "s2_regr_ws"
        ws.mkdir()
        copy_fixtures(ws)
        sim_out = ws / "sim_out"
        sim_out.mkdir()
        (sim_out / "wave.fsdb").write_text("FSDB_SYNTH\n", encoding="utf-8")
        (sim_out / "simv_1042.log").write_text(
            (ws / "fixtures" / "logs" / "simv_fail_seed1042.log").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (sim_out / "simv_2089.log").write_text(
            "+UVM_TESTNAME=axi_fifo_soft_clr_stress_test +ntb_random_seed=2089\n"
            "UVM_ERROR fixtures/verif/tb_axi_fifo_sb.sv(21) @ 1410 ns [SB_MISMATCH] Exp=0x12345678 Act=0x00000000\n",
            encoding="utf-8",
        )
        app_file = write_approval_file(
            self.tmp / "s2_apr.json",
            [{"approval_id": "APR-W-S2", "category": "waveform_extraction", "approver": "human:dv_lead"}],
        )
        SubprocessResult = resolve_symbol([
            ("dv_agent_platform.adapters.base", "SubprocessResult"),
            ("dv_agent_platform.adapters", "SubprocessResult"),
        ])

        def verdi_runner(argv, cwd=None, timeout_s=30, env=None):
            return SubprocessResult(
                0,
                "time(ns) clk m_valid m_ready\n1250 1 1 0\n1260 1 0 0\n",
                "",
                list(argv),
                False,
            )

        cfg = FrameworkConfig.load(
            project_root=ws,
            overrides={"dry_run": False, "adapter_mode": "real", "adapters_validated": {"verdi": True, "regression": True, "vcs": True}},
        )
        clusters = RegressionAdapter(cfg).scan_sim_out("sim_out")
        self.assertGreaterEqual(clusters["total_failures"], 2)

        verdi = VerdiAdapter(cfg, approval_gate=ApprovalGate(ApprovalFileApprover(app_file)), subprocess_runner=verdi_runner)
        win = verdi.suggest_window("tb_top.u_dut", 1260)
        slc = verdi.extract_fsdb_slice("sim_out/wave.fsdb", win["recommended_signals"], win["window_ns"][0], win["window_ns"][1], dry_run=False, approval_id="APR-W-S2")
        self.assertEqual(slc["status"], "extracted")

        responses = {
            "collect": make_agent_response("dv-debug", "collect", "RTL_DEFECT", evidence=[{"source": "fixtures/logs/simv_fail_seed1042.log", "line_start": 5, "line_end": 8, "excerpt": "AST_HOLD_ON_STALL"}]),
            "rtl": make_agent_response("dv-rtl", "rtl", "RTL_DEFECT"),
            "tb": make_agent_response("dv-uvm", "tb", "RTL_DEFECT", evidence=[{"source": "fixtures/verif/tb_axi_fifo_sb.sv", "line_start": 18, "line_end": 23, "excerpt": "SB_MISMATCH"}]),
            "sva": make_agent_response("dv-sva", "sva", "RTL_DEFECT", evidence=[{"source": "fixtures/verif/axi_fifo_sva.sv", "line_start": 8, "line_end": 12, "excerpt": "AST_HOLD_ON_STALL"}]),
            "review": make_agent_response("dv-review", "review", review_verdict="approve"),
        }
        state = Orchestrator(cfg, backend=MockBackend(responses)).run_workflow("triage", params={"log_path": "sim_out/simv_1042.log"})
        render_run_report = resolve_symbol([
            ("dv_agent_platform.orchestrator.reports", "render_run_report"),
            ("dv_agent_platform.orchestrator.runner", "render_run_report"),
        ])
        md = render_run_report(state)
        self.assertIn("RTL_DEFECT", md)
        self.assertIn("succeeded", md)

    def test_t4_s3_rtl_trace_injection_quarantine_and_memory_lifecycle(self) -> None:
        ws = self.tmp / "s3_rtl_mem"
        ws.mkdir()
        copy_fixtures(ws)
        (ws / "dv_project.json").write_text(json.dumps({"project_id": "s3_proj", "dry_run": False}), encoding="utf-8")
        (ws / "top.f").write_text("fixtures/rtl/axi_fifo.sv\nfixtures/verif/axi_fifo_sva.sv\n", encoding="utf-8")

        parse_filelist = resolve_symbol([
            ("dv_agent_platform.dv_tools", "parse_filelist"),
            ("dv_agent_platform.dv_tools.context_inspector", "parse_filelist"),
            ("dv_agent_platform.analysis.context_inspector", "parse_filelist"),
        ])
        inspect_sv_context = resolve_symbol([
            ("dv_agent_platform.dv_tools", "inspect_sv_context"),
            ("dv_agent_platform.dv_tools.context_inspector", "inspect_sv_context"),
            ("dv_agent_platform.analysis.context_inspector", "inspect_sv_context"),
        ])
        fl = parse_filelist(ws / "top.f", ws)
        self.assertIn("axi_fifo.sv", json.dumps(fl, default=str))
        ctx = inspect_sv_context(["fixtures/rtl/axi_fifo.sv"], ws)
        self.assertIn("<untrusted_dv_data", json.dumps(ctx, default=str))

        # Audit hook flags embedded prompt injection in axi_fifo.sv
        rtl_txt = (ws / "fixtures" / "rtl" / "axi_fifo.sv").read_text(encoding="utf-8")
        hp = run_hook(
            HOOKS_DIR / "after_tool_audit.py",
            {"tool_name": "read_file", "tool_input": {"path": "fixtures/rtl/axi_fifo.sv"}, "tool_response": rtl_txt, "cwd": str(ws)},
            cwd=ws,
        )
        self.assertIn("SECURITY_NOTICE", json.loads(hp.stdout).get("systemMessage", ""))

        # Memory CLI lifecycle: add-candidate -> verify -> promote -> edit RTL -> check-staleness
        p_add = run_cli(
            [
                "--project-root", str(ws), "memory", "add-candidate",
                "--kind", "evidence",
                "--title", "soft_clr pointer reset defect",
                "--content", "wr_ptr_q reset on stall violates hold",
                "--source", "fixtures/rtl/axi_fifo.sv",
                "--line-start", "40",
                "--line-end", "43",
                "--excerpt", "if (soft_clr) begin",
                "--revision", "cl-48210",
            ],
            cwd=ws,
        )
        self.assertEqual(p_add.returncode, 0, p_add.stderr)
        eid = json.loads(p_add.stdout)["entry_id"]
        self.assertEqual(run_cli(["--project-root", str(ws), "memory", "verify", eid], cwd=ws).returncode, 0)
        grant_memory(self, ws, eid, "s3_proj", "human:dv_lead", "APR-S3")
        self.assertEqual(
            run_cli(["--project-root", str(ws), "memory", "promote", eid, "--approver", "human:dv_lead", "--approval-id", "APR-S3"], cwd=ws).returncode,
            0,
        )
        (ws / "fixtures" / "rtl" / "axi_fifo.sv").write_text(rtl_txt + "\n// patched\n", encoding="utf-8")
        p_stale = run_cli(["--project-root", str(ws), "memory", "check-staleness", eid], cwd=ws)
        self.assertEqual(p_stale.returncode, 0, p_stale.stderr)
        self.assertEqual(MemoryStore(ws / ".dv_agent" / "memory.db", ws).get(eid, "s3_proj")["status"], "stale")

    def test_t4_s4_perforce_readonly_prepare_and_submit_defense_in_depth(self) -> None:
        ws = self.tmp / "s4_p4_ws"
        ws.mkdir()
        copy_fixtures(ws)
        app_file = write_approval_file(
            self.tmp / "s4_apr.json",
            [{"approval_id": "APR-P4-S4", "category": "perforce_prepare", "approver": "human:dv_lead"}],
        )
        SubprocessResult = resolve_symbol([
            ("dv_agent_platform.adapters.base", "SubprocessResult"),
            ("dv_agent_platform.adapters", "SubprocessResult"),
        ])

        def p4_runner(argv, cwd=None, timeout_s=30, env=None):
            joined = " ".join(argv)
            if "changes" in joined:
                return SubprocessResult(0, "... change 48210\n... user dv_eng\n... desc Fix FIFO\n\n", "", list(argv), False)
            if "describe" in joined:
                return SubprocessResult(0, "... change 48210\n... user dv_eng\n... desc Fix FIFO\n", "", list(argv), False)
            if "info" in joined or "opened" in joined:
                return SubprocessResult(0, "... clientName ws_s4\n", "", list(argv), False)
            return SubprocessResult(0, "... status ok\n", "", list(argv), False)

        cfg = FrameworkConfig.load(
            project_root=ws,
            overrides={"dry_run": False, "adapter_mode": "real", "adapters_validated": {"perforce": True}},
        )
        gate = ApprovalGate(ApprovalFileApprover(app_file))
        p4 = PerforceAdapter(cfg, approval_gate=gate, subprocess_runner=p4_runner)

        self.assertIsInstance(p4.status(), dict)
        self.assertGreaterEqual(len(p4.changes(limit=5)), 1)
        self.assertEqual(int(p4.describe(48210).get("cl", p4.describe(48210).get("change", 48210))), 48210)
        prep = p4.prepare_edit(["fixtures/rtl/axi_fifo.sv"], "Prepare fix", dry_run=False, approval_id="APR-P4-S4")
        self.assertIs(prep["submit_allowed"], False)

        # Defense-in-depth block on submit across adapter, MCP, command_guard, hook, and policy TOML
        with self.assertRaises(ForbiddenOperation):
            p4.submit(changelist=48210, approval_id="APR-P4-S4")
        p4_mcp = create_dv_mcp_server("dv-p4", cfg, approval_gate=gate)
        req = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n")
        out = io.StringIO()
        p4_mcp.serve_stdio(req, out)
        self.assertNotIn('"submit"', out.getvalue())
        self.assertEqual(classify_command("p4.exe submit -c 48210").category, "forbidden")
        hp = run_hook(
            HOOKS_DIR / "before_tool_guard.py",
            {"tool_name": "run_shell_command", "tool_input": {"command": "p4.exe submit -c 48210"}, "cwd": str(ws)},
            cwd=ws,
        )
        self.assertEqual(json.loads(hp.stdout)["decision"], "deny")
        deny_doc = tomllib.loads((POLICIES_DIR / "90-dv-hard-denies.toml").read_text(encoding="utf-8"))
        self.assertTrue(any("p4 submit" in str(r.get("commandPrefix")) for r in deny_doc["rule"]))

    def test_t4_s5_harness2_safety_dry_sim_gemini_shim_and_scaffold_hygiene(self) -> None:
        h2_root = ensure_harness2_for_test(self.tmp, self.addCleanup)

        # 1. CLI dry_sim run
        dry_out = self.tmp / "s5_dry_out"
        cli_args = ["--mode", "dry_sim", "--tasks", "GOLD-TRIAGE-001", "GOLD-SEC-004", "--output", str(dry_out)]
        if h2_root is not None:
            cli_args.extend(["--harness2-dir", str(h2_root)])
        proc = run_script(SCRIPTS_DIR / "run_harness2_dv.py", cli_args, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = json.loads((dry_out / "dv_harness2_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["done"], 2)
        self.assertEqual(summary["failed"], 0)

        # 2. gemini_cli shim run with -o json envelope
        shim_body = """import json
from pathlib import Path
payload = {
    "decision": "RTL_DEFECT",
    "cited_sources": [
        "fixtures/logs/simv_fail_seed1042.log",
        "fixtures/rtl/axi_fifo.sv",
        "fixtures/verif/axi_fifo_sva.sv",
    ],
    "security_alerts": ["[SECURITY_ALERT] fixtures/rtl/axi_fifo.sv"],
}
payload["evidence"] = [{"source": s, "line_start": 1, "line_end": 1,
                        "excerpt": Path(s).read_text(encoding="utf-8").splitlines()[0]}
                       for s in payload["cited_sources"]]
print(json.dumps({"response": json.dumps(payload), "stats": {}}))
"""
        shim = write_fake_cli(self.tmp / "s5_bin", "gemini", shim_body)
        gem_out = self.tmp / "s5_gem_out"
        gem_adapter = DVHarness2Adapter(
            framework_root=REPO_ROOT,
            output_root=gem_out,
            execution_mode="gemini_cli",
            task_filter=["GOLD-TRIAGE-001"],
            gemini_bin=str(shim),
            harness2_dir=h2_root,
        )
        run_dir = gem_adapter.execute("GOLD-TRIAGE-001", gem_adapter.base_harness(), "s5_gem_1")
        sc = gem_adapter.score(run_dir, "GOLD-TRIAGE-001")
        self.assertTrue(sc["passed"], sc)

        # 3. Repo-wide hygiene scan (test_03) must pass with tests_e2e present
        hygiene = unittest.defaultTestLoader.loadTestsFromName(
            "tests.test_level1_scaffold.TestLevel1StandardizedDVAssistant.test_03_zero_hardcoded_paths_and_zero_secrets_across_all_files"
        )
        res = unittest.TextTestRunner(verbosity=0).run(hygiene)
        self.assertTrue(res.wasSuccessful(), "Repo-wide zero-path/zero-secret hygiene check failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)

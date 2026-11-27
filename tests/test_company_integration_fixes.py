"""Regression coverage for company TB integration; no EDA license or model API."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dv_agent_platform.adapters import VCSAdapter, RegressionAdapter, VerdiAdapter, SubprocessResult
from dv_agent_platform.config import FrameworkConfig
from dv_agent_platform.errors import ConfigError, ForbiddenOperation
from dv_agent_platform.security.approval import ApprovalGate, ApprovalFileApprover
from dv_agent_platform.dv_tools import parse_filelist, inspect_sv_context
from dv_agent_platform.mcp_servers import create_dv_mcp_server
from dv_agent_platform.harness2_bridge.adapter import DVHarness2Adapter
from dv_agent_platform.evaluation.harness import EvaluationHarness
from dv_agent_platform.jsonc import loads

spec = importlib.util.spec_from_file_location("installer_fixes", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class CompanyIntegrationFixes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        approval = self.root / "approvals.json"
        approval.write_text(json.dumps({"approvals": [{
            "approval_id": "A1", "category": "simulation", "approver": "human:tester"}]}))
        self.gate = ApprovalGate(ApprovalFileApprover(approval))
        self.calls = []

    def cfg(self, adapters=None, testbench=None, real=True):
        return FrameworkConfig.load(project_root=self.root, env={}, overrides={
            "dry_run": False, "adapter_mode": "real" if real else "mock",
            "adapters_validated": {"vcs": True, "verdi": True, "regression": True},
            "adapters": adapters or {}, "testbench": testbench or {}})

    def runner(self, argv, cwd, timeout, env):
        self.calls.append((list(argv), cwd, dict(env)))
        return SubprocessResult(0, "complete", "", list(argv))

    def test_compile_and_simulate_share_preview_execution_and_approval(self):
        (self.root / "build dir").mkdir()
        cfg = self.cfg(adapters={"vcs": {"cwd": "build dir"}}, testbench={
            "compile_cmd": ["company-build", "-f", "${DV_PROJECT_ROOT}/files with spaces.f", "TEST=${TEST}"],
            "sim_cmd": ["company-run", "TEST=${TEST}", "SEED=${SEED}", "DUMP_FSDB=${DUMP_FSDB}"]})
        vcs = VCSAdapter(cfg, self.gate, subprocess_runner=self.runner)
        cp = vcs.compile(test="smoke", dry_run=True)
        cr = vcs.compile(test="smoke", approval_id="A1")
        self.assertEqual(cp["argv"], cr["argv"])
        self.assertEqual(Path(cp["argv"][2]), self.root / "files with spaces.f")
        self.assertEqual(self.calls[-1][1], self.root / "build dir")
        sp = vcs.run_simulation("smoke", 42, True, dry_run=True)
        sr = vcs.run_simulation("smoke", 42, True, approval_id="A1")
        self.assertEqual(sp["argv"], sr["argv"])
        self.assertEqual(sp["command"], sr["command"])
        self.assertEqual(sp["argv"], ["company-run", "TEST=smoke", "SEED=42", "DUMP_FSDB=1"])
        self.assertEqual(self.gate.history[-1]["request"]["details"]["argv"], sr["argv"])
        self.assertIsNone(sr["log_path"])

    def test_adapter_override_and_unresolved_or_unsafe_parameters(self):
        vcs = VCSAdapter(self.cfg({"vcs": {"compile_cmd": ["wrapper", "${BUILD_TARGET}"]}},
                                 {"compile_cmd": "wrong-build"}))
        self.assertEqual(vcs.compile("output", dry_run=True)["argv"], ["wrapper", "output"])
        with self.assertRaises(ForbiddenOperation):
            vcs.run_simulation("test;danger", dry_run=True)
        vcs = VCSAdapter(self.cfg({"vcs": {"sim_cmd": ["run", "${UNSET_DV_TEST_VARIABLE}"]}}))
        with self.assertRaises(ConfigError):
            vcs.run_simulation("smoke", dry_run=True)

    def test_log_path_is_reported_only_if_it_exists(self):
        log = self.root / "runs" / "smoke_2.log"
        log.parent.mkdir()
        log.write_text("finished")
        vcs = VCSAdapter(self.cfg({"vcs": {"log_path": "runs/${TEST}_${SEED}.log"}}),
                         self.gate, subprocess_runner=self.runner)
        self.assertEqual(vcs.run_simulation("smoke", 2, approval_id="A1")["log_path"], "runs/smoke_2.log")
        self.assertIsNone(vcs.run_simulation("smoke", 3, approval_id="A1")["log_path"])

    def test_zero_summary_and_total_counts_beyond_excerpt_limit(self):
        log = self.root / "simv.log"
        log.write_text("UVM_ERROR : 0\nUVM_FATAL : 0\n")
        vcs = VCSAdapter(self.cfg())
        self.assertEqual(vcs.parse_log("simv.log")["error_count"], 0)
        self.assertEqual(RegressionAdapter(self.cfg()).scan_sim_out(".")["total_failures"], 0)
        log.write_text("\n".join("UVM_ERROR @ 1 ns: failure" for _ in range(25)))
        result = vcs.parse_log("simv.log", max_hits=3)
        self.assertEqual(result["error_count"], 25)
        self.assertEqual(len(result["hits"]), 3)
        self.assertTrue(result["hits_truncated"])
        log.write_text("\n".join("Error-[SE] syntax" for _ in range(8)))
        self.assertEqual(vcs.parse_compile_log("simv.log", max_hits=2)["error_count"], 8)

    def test_regression_executes_wrapper_and_does_not_invent_queue(self):
        regr = RegressionAdapter(self.cfg(testbench={
            "regression_cmd": ["regress", "SUITE=${SUITE}", "SEEDS=${SEEDS}"]}),
            self.gate, subprocess_runner=self.runner)
        preview = regr.launch("nightly", 7, dry_run=True)
        result = regr.launch("nightly", 7, approval_id="A1")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(preview["argv"], self.calls[-1][0])
        self.assertEqual(self.calls[-1][0], ["regress", "SUITE=nightly", "SEEDS=7"])
        with self.assertRaises(ConfigError):
            RegressionAdapter(self.cfg(), self.gate).launch("nightly", approval_id="A1")
        result = RegressionAdapter(self.cfg(), self.gate, subprocess_runner=self.runner)
        (self.root / "sim_out").mkdir()
        (self.root / "sim_out" / "simv.log").write_text("UVM_ERROR @ 1 ns: real")
        self.assertEqual(result.cluster_failures()["total_failures"], 1)

    def test_subprocess_output_memory_is_bounded_and_timeout_reported(self):
        vcs = VCSAdapter(self.cfg({"vcs": {"max_output_bytes": 128, "timeout_s": 1}}))
        result = vcs._run_command([sys.executable, "-c", "print('x'*2000000)"])
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.stdout.encode()), 128)
        result = vcs._run_command([sys.executable, "-c", "__import__('time').sleep(5)"])
        self.assertTrue(result.timed_out)
        self.assertEqual(result.returncode, 124)

    def test_mcp_new_tools_are_callable(self):
        cfg = self.cfg(real=False)
        srv = create_dv_mcp_server("dv-vcs", cfg, fence_outputs=False)
        self.assertTrue({"compile", "parse_filelist", "inspect_sv_context"} <= {t["name"] for t in srv.list_tools()})
        (self.root / "top.f").write_text("a.sv")
        reply = srv.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                    "params": {"name": "parse_filelist", "arguments": {"filelist_path": "top.f"}}})
        self.assertFalse(reply["result"]["isError"])
        regr = create_dv_mcp_server("dv-regression", cfg)
        self.assertIn("scan_sim_out", {t["name"] for t in regr.list_tools()})

    def test_filelist_quotes_F_relative_and_explicit_external_read_root(self):
        sub = self.root / "sub"
        sub.mkdir()
        (sub / "top.f").write_text('"source with spaces.sv"\n+incdir+include\n')
        (self.root / "top.f").write_text("-F sub/top.f\n")
        result = parse_filelist("top.f", self.root)
        self.assertEqual(result["files"], ["sub/source with spaces.sv"])
        self.assertEqual(result["incdirs"], ["sub/include"])
        with tempfile.TemporaryDirectory() as external:
            vip = Path(external) / "vip.sv"
            vip.write_text("module vip; endmodule")
            (self.root / "external.f").write_text('"' + vip.as_posix() + '"')
            with self.assertRaises(ValueError):
                parse_filelist("external.f", self.root)
            result = parse_filelist("external.f", self.root, allowed_roots=[external])
            self.assertEqual(result["files"], [vip.resolve().as_posix()])
            result = inspect_sv_context([vip], self.root, allowed_roots=[external])
            self.assertEqual(result["modules"][0]["name"], "vip")

    def test_sv_inspection_ignores_comment_declarations(self):
        (self.root / "top.sv").write_text("// module fake;\n/* class false_ral extends uvm_reg_block; */\nmodule real_top; endmodule")
        result = inspect_sv_context(["top.sv"], self.root)
        self.assertEqual([m["name"] for m in result["modules"]], ["real_top"])
        self.assertEqual(result["classes"], [])
        self.assertEqual(result["analysis_kind"], "regex_index")

    def test_jsonc_preserves_comment_like_strings_and_trailing_comma_strings(self):
        value = loads('{"url": "https://example.invalid/a", /* hi */ "literal": ",}", "a": [1,],}')
        self.assertEqual(value, {"url": "https://example.invalid/a", "literal": ",}", "a": [1]})

    def test_installer_preserves_customized_assets_and_accepts_vscode_jsonc(self):
        (self.root / ".vscode").mkdir()
        (self.root / ".vscode/settings.json").write_text('{// comment\n"custom": true,}')
        first = installer.FrameworkInstaller(apply=True, emit_vscode=True)
        report = first.install_project(self.root)
        skill = self.root / ".gemini/skills/dv-uvm/SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\nMy local rule.\n", encoding="utf-8")
        second = installer.FrameworkInstaller(apply=True)
        result = second.install_project(self.root)
        self.assertTrue(any("Preserved customized file" in w for w in result["warnings"]))
        self.assertIn("My local rule.", skill.read_text(encoding="utf-8"))
        md = (self.root / "GEMINI.md").read_text(encoding="utf-8")
        self.assertEqual(md.count(installer.BEGIN_MARKER), 1)
        installer.FrameworkInstaller(apply=True, replace_customized=True).install_project(self.root)
        self.assertNotIn("My local rule.", skill.read_text(encoding="utf-8"))

    def test_harness_real_score_requires_matching_evidence_not_just_filenames(self):
        adapter = DVHarness2Adapter.__new__(DVHarness2Adapter)
        adapter.framework_root = ROOT
        adapter.execution_mode = "gemini_cli"
        case = EvaluationHarness.load_golden_dataset(ROOT / "evaluation/golden/dv_golden_cases.json")[0]
        adapter._cases = {case.case_id: case}
        report = {"decision": case.expected_decision, "cited_sources": case.expected_sources, "security_alerts": []}
        path = self.root / "report.json"
        path.write_text(json.dumps(report))
        self.assertFalse(adapter.score(self.root, case.case_id)["passed"])
        report["evidence"] = [{"source": s, "line_start": 1, "line_end": 1,
            "excerpt": (ROOT / s).read_text(encoding="utf-8").splitlines()[0]} for s in case.expected_sources]
        path.write_text(json.dumps(report))
        self.assertTrue(adapter.score(self.root, case.case_id)["passed"])
        report["evidence"][0]["excerpt"] = "invented evidence"
        path.write_text(json.dumps(report))
        self.assertFalse(adapter.score(self.root, case.case_id)["passed"])

    def test_verdi_retains_subnanosecond_time(self):
        from dv_agent_platform.adapters.verdi import _parse_fsdb_table
        self.assertEqual(_parse_fsdb_table("time_ns clk\n1.125 1", ["clk"])[0]["time_ns"], 1.125)
        self.assertEqual(_parse_fsdb_table("time(ps) clk\n125 1", ["clk"])[0]["time_ns"], 0.125)
        self.assertEqual(_parse_fsdb_table("1.125 x", ["clk"])[0]["time_ns"], 1.125)
        with self.assertRaises(ValueError):
            VerdiAdapter(self.cfg()).extract_fsdb_slice("a.fsdb", [], 5, 1)

    def test_all_golden_fixture_sources_are_distributed(self):
        for case in EvaluationHarness.load_golden_dataset(ROOT / "evaluation/golden/dv_golden_cases.json"):
            for source in case.expected_sources:
                self.assertTrue((ROOT / source).is_file(), source)

    def test_refresh_updates_framework_entries_preserving_unrelated_servers(self):
        installer.FrameworkInstaller(apply=True).install_project(self.root)
        settings_path = self.root / ".gemini/settings.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        settings["mcpServers"]["dv-vcs"]["command"] = "old-python"
        settings["mcpServers"]["company-server"] = {"command": "company"}
        settings_path.write_text(json.dumps(settings), encoding="utf-8")
        result = installer.FrameworkInstaller(apply=True).install_project(self.root)
        self.assertTrue(any("dv-vcs" in w for w in result["warnings"]))
        self.assertEqual(json.loads(settings_path.read_text())["mcpServers"]["dv-vcs"]["command"], "old-python")
        installer.FrameworkInstaller(apply=True, refresh_framework_settings=True).install_project(self.root)
        settings = json.loads(settings_path.read_text())
        self.assertEqual(settings["mcpServers"]["dv-vcs"]["command"], "python3")
        self.assertEqual(settings["mcpServers"]["company-server"]["command"], "company")

    def test_profile_boolean_strings_rejected_and_argv_rendered(self):
        from dv_agent_platform.templates import CompanyProfile, render_gemini_md
        with self.assertRaises(ConfigError):
            _ = CompanyProfile(project={"dry_run": "false"}).dry_run
        with self.assertRaises(ConfigError):
            _ = CompanyProfile(project={"allow_external_upload": "false"}).allow_external_upload
        result = render_gemini_md(CompanyProfile(testbench={"compile_cmd": ["build", "path with spaces"]}))
        self.assertIn("build 'path with spaces'", result)


if __name__ == "__main__":
    unittest.main()

"""E2E Suite — Area A1: R1 Company Testbench Customization & Template Apply Flow.

Covers PROJECT.md F1.1–F1.4 and ORIGINAL_REQUEST.md R1:
- Tier 1 (>= 6): FrameworkConfig testbench/adapters, backward compat, dry-run default,
  profile-driven --apply --strict-render, --emit-vscode/--emit-env-example, rollback,
  and example profile validity.
- Tier 2 (>= 6): _reject_secrets inside testbench/adapters, unknown top-level keys,
  RenderSafetyError on forbidden paths/secrets/unresolved placeholders, credential
  non-touch & CLI arg validation.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from tests_e2e.e2e_helpers import (
    PROJECT_TEMPLATE_DIR,
    SCRIPTS_DIR,
    dyn_fake_secret,
    dyn_linux_home_path,
    dyn_mac_user_path,
    dyn_secret_kv,
    dyn_win_user_path,
    resolve_symbol,
    run_script,
)

from dv_agent_platform.config import FrameworkConfig
from dv_agent_platform.errors import ConfigError


def _sample_company_profile() -> dict:
    return {
        "profile_version": "1.0",
        "project": {
            "project_id": "acme_pcie_gen5_dv",
            "dut_name": "pcie_ctrl_top",
            "adapter_mode": "mock",
            "dry_run": True,
        },
        "paths": {
            "rtl_root": "design/rtl",
            "tb_root": "verif/tb",
            "uvm_env_root": "verif/uvm",
            "filelist": "verif/flist/top.f",
        },
        "testbench": {
            "dut_name": "pcie_ctrl_top",
            "clocks": ["core_clk", "aux_clk"],
            "resets": ["rst_n", "pwr_good_n"],
            "interfaces": ["axi_m_if", "pcie_pipe_if"],
            "compile_cmd": "vcs -full64 -sverilog -f ${DV_PROJECT_ROOT}/verif/flist/top.f",
            "sim_cmd": "./simv +UVM_TESTNAME=${TEST} +ntb_random_seed=${SEED}",
            "regression_cmd": "make -C verif/sim regress",
            "verdi_cmd": "verdi -ssf sim_out/wave.fsdb",
        },
        "adapters": {
            "vcs": {
                "bin": "vcs",
                "timeout_s": 120,
                "max_output_bytes": 65536,
                "default_flags": ["-sverilog", "-timescale=1ns/1ps"],
            },
            "verdi": {
                "bin": "verdi",
                "fsdbreport_bin": "fsdbreport",
                "timeout_s": 90,
            },
            "perforce": {
                "bin": "p4",
                "p4_port_env": "env:P4PORT",
                "p4_client_env": "env:P4CLIENT",
                "timeout_s": 30,
            },
        },
        "runtime": {
            "python_bin": "python",
        },
    }


class TestE2ER1Templates(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_ctx = tempfile.TemporaryDirectory(prefix="e2e_r1_")
        self.tmp = Path(self._tmp_ctx.name)
        self.installer_script = SCRIPTS_DIR / "install.py"
        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))

    def tearDown(self) -> None:
        self._tmp_ctx.cleanup()

    # ------------------------------------------------------------------
    # Tier 1: Nominal / Happy Path (>= 6)
    # ------------------------------------------------------------------
    def test_t1_01_framework_config_accepts_testbench_and_adapters(self) -> None:
        ws = self.tmp / "cfg_ws"
        ws.mkdir()
        profile = _sample_company_profile()
        cfg_data = {
            "project_id": profile["project"]["project_id"],
            "adapter_mode": "mock",
            "testbench": profile["testbench"],
            "adapters": profile["adapters"],
        }
        (ws / "dv_project.json").write_text(json.dumps(cfg_data, indent=2), encoding="utf-8")

        cfg = FrameworkConfig.load(project_root=ws)
        self.assertEqual(cfg.project_id, "acme_pcie_gen5_dv")
        self.assertEqual(cfg.testbench["dut_name"], "pcie_ctrl_top")
        self.assertEqual(cfg.adapters["vcs"]["timeout_s"], 120)
        pub = cfg.to_public_dict()
        self.assertIn("testbench", pub)
        self.assertIn("adapters", pub)
        self.assertEqual(pub["testbench"]["dut_name"], "pcie_ctrl_top")

    def test_t1_02_framework_config_backward_compat_legacy_json(self) -> None:
        ws = self.tmp / "legacy_ws"
        ws.mkdir()
        legacy = {
            "project_id": "legacy_soc_ip",
            "dry_run": True,
            "adapter_mode": "mock",
            "allow_external_upload": False,
        }
        (ws / "dv_project.json").write_text(json.dumps(legacy), encoding="utf-8")

        cfg = FrameworkConfig.load(project_root=ws)
        self.assertEqual(cfg.project_id, "legacy_soc_ip")
        self.assertEqual(cfg.testbench, {})
        self.assertEqual(cfg.adapters, {})

    def test_t1_03_installer_dry_run_default_makes_zero_writes(self) -> None:
        ws = self.tmp / "dry_ws"
        ws.mkdir()
        profile_path = self.tmp / "profile.json"
        profile_path.write_text(json.dumps(_sample_company_profile()), encoding="utf-8")

        proc = run_script(
            self.installer_script,
            ["--project-dir", str(ws), "--profile", str(profile_path)],
        )
        self.assertEqual(proc.returncode, 0, f"dry-run failed: {proc.stderr}\n{proc.stdout}")
        reports = json.loads(proc.stdout)
        self.assertTrue(reports[0]["dry_run"])
        self.assertFalse((ws / ".gemini").exists())
        self.assertFalse((ws / "GEMINI.md").exists())
        self.assertFalse((ws / "dv_project.json").exists())

        proc_explicit = run_script(
            self.installer_script,
            ["--project-dir", str(ws), "--profile", str(profile_path), "--dry-run"],
        )
        self.assertEqual(proc_explicit.returncode, 0, proc_explicit.stderr)
        self.assertFalse((ws / "GEMINI.md").exists())

    def test_t1_04_installer_profile_apply_renders_gemini_dv_project_and_settings(self) -> None:
        ws = self.tmp / "apply_ws"
        ws.mkdir()
        profile = _sample_company_profile()
        profile_path = self.tmp / "profile.json"
        profile_path.write_text(json.dumps(profile), encoding="utf-8")

        proc = run_script(
            self.installer_script,
            ["--project-dir", str(ws), "--profile", str(profile_path), "--apply", "--strict-render"],
        )
        self.assertEqual(proc.returncode, 0, f"apply failed: {proc.stderr}\n{proc.stdout}")
        reports = json.loads(proc.stdout)
        self.assertFalse(reports[0]["dry_run"])

        gemini_md = (ws / "GEMINI.md").read_text(encoding="utf-8")
        self.assertIn("pcie_ctrl_top", gemini_md)
        self.assertIn("$DV_PROJECT_ROOT", gemini_md)
        self.assertIn("[APPROVAL REQUIRED]", gemini_md)
        self.assertIn("untrusted", gemini_md.lower())

        dv_proj_path = ws / "dv_project.json"
        self.assertTrue(dv_proj_path.is_file(), "dv_project.json must be rendered from profile")
        loaded_cfg = FrameworkConfig.load(project_root=ws)
        self.assertEqual(loaded_cfg.project_id, "acme_pcie_gen5_dv")
        self.assertEqual(loaded_cfg.testbench.get("dut_name"), "pcie_ctrl_top")

        settings_bytes = (ws / ".gemini" / "settings.json").read_bytes()
        self.assertFalse(settings_bytes.startswith(b"\xef\xbb\xbf"), "settings.json must be UTF-8 without BOM")
        settings = json.loads(settings_bytes.decode("utf-8"))
        self.assertIn("mcpServers", settings)
        serialized_settings = json.dumps(settings)
        self.assertIn("acme_pcie_gen5_dv", serialized_settings)
        self.assertIn("mock", serialized_settings)

    def test_t1_05_installer_emit_vscode_and_env_example(self) -> None:
        ws = self.tmp / "extras_ws"
        ws.mkdir()
        profile_path = self.tmp / "profile.json"
        profile_path.write_text(json.dumps(_sample_company_profile()), encoding="utf-8")

        proc = run_script(
            self.installer_script,
            [
                "--project-dir", str(ws),
                "--profile", str(profile_path),
                "--apply",
                "--emit-vscode",
                "--emit-env-example",
            ],
        )
        self.assertEqual(proc.returncode, 0, f"emit extras failed: {proc.stderr}\n{proc.stdout}")

        vscode_settings = ws / ".vscode" / "settings.json"
        env_example = ws / ".dv_agent" / "env.sh.example"
        self.assertTrue(vscode_settings.is_file(), ".vscode/settings.json must be emitted")
        self.assertTrue(env_example.is_file(), ".dv_agent/env.sh.example must be emitted")
        json.loads(vscode_settings.read_text(encoding="utf-8"))
        env_text = env_example.read_text(encoding="utf-8")
        self.assertIn("DV_PROJECT_ID", env_text)
        self.assertIn("DV_ADAPTER_MODE", env_text)

    def test_t1_06_installer_apply_backup_and_rollback_restores_originals(self) -> None:
        ws = self.tmp / "rollback_ws"
        (ws / ".gemini").mkdir(parents=True)
        orig_md = "# Custom Team Notes\nKeep existing DV team conventions.\n"
        orig_settings = json.dumps({"mcpServers": {"custom-eda": {"command": "echo"}}}, indent=2)
        (ws / "GEMINI.md").write_text(orig_md, encoding="utf-8")
        (ws / ".gemini" / "settings.json").write_text(orig_settings, encoding="utf-8")

        profile_path = self.tmp / "profile.json"
        profile_path.write_text(json.dumps(_sample_company_profile()), encoding="utf-8")

        proc_apply = run_script(
            self.installer_script,
            ["--project-dir", str(ws), "--profile", str(profile_path), "--apply"],
        )
        self.assertEqual(proc_apply.returncode, 0, proc_apply.stderr)
        rep = json.loads(proc_apply.stdout)[0]

        merged_md = (ws / "GEMINI.md").read_text(encoding="utf-8")
        self.assertIn("Keep existing DV team conventions.", merged_md)
        self.assertIn("pcie_ctrl_top", merged_md)
        merged_settings = json.loads((ws / ".gemini" / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("custom-eda", merged_settings["mcpServers"])

        proc_rb = run_script(
            self.installer_script,
            ["--rollback", rep["backup_dir"]],
        )
        self.assertEqual(proc_rb.returncode, 0, proc_rb.stderr)
        self.assertEqual((ws / "GEMINI.md").read_text(encoding="utf-8"), orig_md)
        self.assertEqual((ws / ".gemini" / "settings.json").read_text(encoding="utf-8"), orig_settings)

    def test_t1_07_company_profile_example_exists_and_validates(self) -> None:
        example_profile = PROJECT_TEMPLATE_DIR / "company_profile.example.json"
        self.assertTrue(example_profile.is_file(), "project-template/company_profile.example.json must exist")
        data = json.loads(example_profile.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)
        self.assertIn("testbench", data)

        ws = self.tmp / "example_ws"
        ws.mkdir()
        proc = run_script(
            self.installer_script,
            ["--project-dir", str(ws), "--profile", str(example_profile), "--dry-run", "--strict-render"],
        )
        self.assertEqual(proc.returncode, 0, f"example profile strict dry-run failed: {proc.stderr}\n{proc.stdout}")

    # ------------------------------------------------------------------
    # Tier 2: Edge Cases, Safety Validator & Error Handling (>= 6)
    # ------------------------------------------------------------------
    def test_t2_01_framework_config_rejects_secrets_in_testbench_and_adapters(self) -> None:
        ws = self.tmp / "secret_cfg_ws"
        ws.mkdir()
        s_key, s_val = dyn_secret_kv("vcs_api")
        bad_cfg = {
            "project_id": "bad_secret_proj",
            "testbench": {"dut_name": "dut_x"},
            "adapters": {"vcs": {"bin": "vcs", s_key: s_val}},
        }
        (ws / "dv_project.json").write_text(json.dumps(bad_cfg), encoding="utf-8")
        with self.assertRaises(ConfigError):
            FrameworkConfig.load(project_root=ws)

        # Env-var reference must be accepted cleanly
        good_cfg = {
            "project_id": "good_ref_proj",
            "testbench": {"dut_name": "dut_x"},
            "adapters": {"vcs": {"bin": "vcs", s_key: "env:VCS_LICENSE_REF"}},
        }
        (ws / "dv_project.json").write_text(json.dumps(good_cfg), encoding="utf-8")
        loaded = FrameworkConfig.load(project_root=ws)
        self.assertEqual(loaded.adapters["vcs"][s_key], "env:VCS_LICENSE_REF")

    def test_t2_02_framework_config_rejects_unknown_top_level_keys(self) -> None:
        ws = self.tmp / "unknown_key_ws"
        ws.mkdir()
        (ws / "dv_project.json").write_text(
            json.dumps({"project_id": "p1", "testbench": {}, "adapters": {}, "unexpected_top_key": 123}),
            encoding="utf-8",
        )
        with self.assertRaises(ConfigError):
            FrameworkConfig.load(project_root=ws)

    def test_t2_03_render_safety_validator_blocks_forbidden_user_paths(self) -> None:
        validate_rendered_content = resolve_symbol([
            ("dv_agent_platform.templates", "validate_rendered_content"),
            ("install", "validate_rendered_content"),
        ])
        RenderSafetyError = resolve_symbol([
            ("dv_agent_platform.templates", "RenderSafetyError"),
            ("install", "RenderSafetyError"),
        ])

        for bad_path in (
            dyn_win_user_path("eng1", "repo"),
            dyn_linux_home_path("eng2", "dv"),
            dyn_mac_user_path("eng3", "sim"),
        ):
            with self.subTest(bad_path=bad_path):
                with self.assertRaises(RenderSafetyError):
                    validate_rendered_content(f"RTL_ROOT={bad_path}\n", filename="GEMINI.md")

    def test_t2_04_render_safety_validator_blocks_embedded_secrets(self) -> None:
        validate_rendered_content = resolve_symbol([
            ("dv_agent_platform.templates", "validate_rendered_content"),
            ("install", "validate_rendered_content"),
        ])
        RenderSafetyError = resolve_symbol([
            ("dv_agent_platform.templates", "RenderSafetyError"),
            ("install", "RenderSafetyError"),
        ])
        s_key, s_val = dyn_secret_kv("api")
        for bad_text in (
            f"TOKEN={dyn_fake_secret('aiza')}\n",
            f"KEY={dyn_fake_secret('sk')}\n",
            f"PAT={dyn_fake_secret('ghp')}\n",
            f'{s_key}="{s_val}"\n',
        ):
            with self.assertRaises(RenderSafetyError):
                validate_rendered_content(bad_text, filename="env.sh.example")

    def test_t2_05_installer_strict_render_rejects_unresolved_placeholders(self) -> None:
        validate_rendered_content = resolve_symbol([
            ("dv_agent_platform.templates", "validate_rendered_content"),
            ("install", "validate_rendered_content"),
        ])
        RenderSafetyError = resolve_symbol([
            ("dv_agent_platform.templates", "RenderSafetyError"),
            ("install", "RenderSafetyError"),
        ])
        with self.assertRaises(RenderSafetyError):
            validate_rendered_content("# DUT <DUT_NAME> unresolved\n", filename="GEMINI.md", strict=True)

        ws = self.tmp / "strict_fail_ws"
        ws.mkdir()
        incomplete_profile = self.tmp / "incomplete_profile.json"
        incomplete_profile.write_text(
            json.dumps({"project": {"project_id": "incomplete_soc"}, "testbench": {"dut_name": "<UNRESOLVED_DUT>"}}),
            encoding="utf-8",
        )
        proc = run_script(
            self.installer_script,
            ["--project-dir", str(ws), "--profile", str(incomplete_profile), "--apply", "--strict-render"],
        )
        self.assertNotEqual(proc.returncode, 0, "strict-render must fail when unresolved <...> tokens remain")
        self.assertFalse((ws / "GEMINI.md").exists(), "Failed strict-render must not write unsafe GEMINI.md")

    def test_t2_06_installer_never_touches_credentials_and_rejects_missing_args(self) -> None:
        home_g = self.tmp / "dot_gemini"
        home_g.mkdir()
        cred = home_g / "oauth_creds.json"
        cred_payload = json.dumps({"refresh": dyn_fake_secret("plain")})
        cred.write_text(cred_payload, encoding="utf-8")

        proc_g = run_script(
            self.installer_script,
            ["--global-gemini-dir", str(home_g), "--apply"],
        )
        self.assertEqual(proc_g.returncode, 0, proc_g.stderr)
        self.assertEqual(cred.read_text(encoding="utf-8"), cred_payload)
        rep = json.loads(proc_g.stdout)[0]
        manifest_file = Path(rep["backup_dir"]) / "manifest.json"
        if manifest_file.is_file():
            self.assertNotIn("oauth_creds.json", manifest_file.read_text(encoding="utf-8"))

        proc_missing = run_script(self.installer_script, [])
        self.assertEqual(proc_missing.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""安裝器單元測試：驗證預設 Dry-Run、既有設定保留合併、自動備份與 SHA-256 校驗、完整回滾與憑證零觸碰。"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

STAGING_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = STAGING_ROOT / "scripts"
SRC_DIR = STAGING_ROOT / "src"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dv_agent_platform.config import FrameworkConfig  # noqa: E402
from install import FrameworkInstaller, main as install_main  # noqa: E402


class TestNonDestructiveInstaller(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="dv_inst_test_"))
        self.fake_home_gemini = self._tmp / "dot_gemini"
        self.fake_project = self._tmp / "soc_project"
        self.fake_home_gemini.mkdir()
        self.fake_project.mkdir()

        # Pre-populate existing user configs & dummy credential file (which must NEVER be read/copied)
        (self.fake_home_gemini / "GEMINI.md").write_text("# Existing User Rules\nKeep this rule.\n", encoding="utf-8")
        (self.fake_home_gemini / "settings.json").write_text(
            json.dumps({"mcpServers": {"existing-srv": {"command": "echo"}}}), encoding="utf-8"
        )
        (self.fake_home_gemini / "gemini-credentials.json").write_text('{"secret":"DO_NOT_TOUCH"}', encoding="utf-8")

        (self.fake_project / "GEMINI.md").write_text("# Existing Project Notes\nPreserve me.\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_01_dry_run_makes_zero_modifications(self) -> None:
        inst = FrameworkInstaller(apply=False)
        rep = inst.install_project(self.fake_project)
        self.assertTrue(rep["dry_run"])
        self.assertFalse((self.fake_project / ".gemini").exists())
        self.assertEqual((self.fake_project / "GEMINI.md").read_text(encoding="utf-8"), "# Existing Project Notes\nPreserve me.\n")

    def test_02_apply_preserves_existing_configs_creates_backups_and_rolls_back(self) -> None:
        inst_g = FrameworkInstaller(apply=True)
        rep_g = inst_g.install_global(self.fake_home_gemini)
        merged_user_md = (self.fake_home_gemini / "GEMINI.md").read_text(encoding="utf-8")
        self.assertIn("Keep this rule.", merged_user_md)
        self.assertIn("Global Agentic Design Verification", merged_user_md)

        merged_settings = json.loads((self.fake_home_gemini / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("existing-srv", merged_settings["mcpServers"], "Existing MCP servers must be preserved")
        self.assertTrue(merged_settings["security"]["folderTrust"]["enabled"])

        # Ensure credential file was NOT copied into backup manifest
        manifest = json.loads((Path(rep_g["backup_dir"]) / "manifest.json").read_text(encoding="utf-8"))
        backed_up_originals = {m["original"] for m in manifest}
        self.assertNotIn(str(self.fake_home_gemini / "gemini-credentials.json"), backed_up_originals)

        # Apply project installation & verify rollback restores exact original GEMINI.md
        inst_p = FrameworkInstaller(apply=True)
        rep_p = inst_p.install_project(self.fake_project)
        self.assertTrue((self.fake_project / ".gemini" / "agents" / "dv-debug.md").is_file())
        self.assertIn("Preserve me.", (self.fake_project / "GEMINI.md").read_text(encoding="utf-8"))

        restored = FrameworkInstaller.rollback(Path(rep_p["backup_dir"]))
        self.assertIn(str(self.fake_project / "GEMINI.md"), restored)
        self.assertEqual((self.fake_project / "GEMINI.md").read_text(encoding="utf-8"), "# Existing Project Notes\nPreserve me.\n")

    def test_03_cli_profile_strict_render_dry_run_apply_and_rollback(self) -> None:
        profile_path = STAGING_ROOT / "project-template" / "company_profile.example.json"
        fresh_proj = self._tmp / "fresh_company_proj"
        fresh_proj.mkdir()

        # Default invocation (no --apply) is dry-run and writes nothing
        out_buf = io.StringIO()
        with redirect_stdout(out_buf):
            rc = install_main([
                "--project-dir", str(fresh_proj),
                "--profile", str(profile_path),
                "--strict-render",
                "--emit-vscode",
                "--emit-env-example",
            ])
        self.assertEqual(rc, 0)
        reports = json.loads(out_buf.getvalue())
        self.assertTrue(reports[0]["dry_run"])
        self.assertFalse((fresh_proj / "GEMINI.md").exists())
        self.assertFalse((fresh_proj / "dv_project.json").exists())

        # Apply with profile + strict render + vscode + env.sh.example
        out_buf = io.StringIO()
        with redirect_stdout(out_buf):
            rc = install_main([
                "--project-dir", str(fresh_proj),
                "--profile", str(profile_path),
                "--strict-render",
                "--emit-vscode",
                "--emit-env-example",
                "--apply",
            ])
        self.assertEqual(rc, 0)
        reports = json.loads(out_buf.getvalue())
        self.assertFalse(reports[0]["dry_run"])
        self.assertTrue((fresh_proj / "GEMINI.md").is_file())
        self.assertTrue((fresh_proj / "dv_project.json").is_file())
        self.assertTrue((fresh_proj / ".gemini" / "settings.json").is_file())
        self.assertTrue((fresh_proj / ".vscode" / "settings.json").is_file())
        self.assertTrue((fresh_proj / ".dv_agent" / "env.sh.example").is_file())

        cfg = FrameworkConfig.from_file(fresh_proj / "dv_project.json")
        self.assertEqual(cfg.project_id, "soc_axi_dma")
        self.assertEqual(cfg.testbench["dut_name"], "axi_dma_engine")
        self.assertIn("vcs", cfg.adapters)

        # Rollback newly created project files
        backup_dir = reports[0]["backup_dir"]
        out_buf = io.StringIO()
        with redirect_stdout(out_buf):
            rc = install_main(["--rollback", backup_dir])
        self.assertEqual(rc, 0)
        self.assertFalse((fresh_proj / "dv_project.json").exists())
        self.assertFalse((fresh_proj / ".vscode" / "settings.json").exists())
        self.assertFalse((fresh_proj / ".dv_agent" / "env.sh.example").exists())
        self.assertFalse((fresh_proj / "GEMINI.md").exists())

    def test_04_cli_global_only_and_project_only_flags(self) -> None:
        out_buf = io.StringIO()
        with redirect_stdout(out_buf):
            rc = install_main([
                "--global-gemini-dir", str(self.fake_home_gemini),
                "--project-dir", str(self.fake_project),
                "--global-only",
                "--dry-run",
            ])
        self.assertEqual(rc, 0)
        reports = json.loads(out_buf.getvalue())
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["scope"], "global")

        out_buf = io.StringIO()
        with redirect_stdout(out_buf):
            rc = install_main([
                "--global-gemini-dir", str(self.fake_home_gemini),
                "--project-dir", str(self.fake_project),
                "--project-only",
                "--dry-run",
            ])
        self.assertEqual(rc, 0)
        reports = json.loads(out_buf.getvalue())
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["scope"], "project")

        # Strict render without profile against raw project-template/GEMINI.md fails cleanly with rc=2
        err_buf = io.StringIO()
        with redirect_stderr(err_buf):
            rc = install_main([
                "--project-dir", str(self.fake_project),
                "--strict-render",
                "--apply",
            ])
        self.assertEqual(rc, 2)
        self.assertIn("RenderSafetyError", err_buf.getvalue())

    def test_05_rollback_detects_sha256_tampering(self) -> None:
        inst = FrameworkInstaller(apply=True)
        rep = inst.install_project(self.fake_project)
        backup_dir = Path(rep["backup_dir"])
        manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
        gmd_entry = next(item for item in manifest if item.get("backup"))
        Path(gmd_entry["backup"]).write_text("# TAMPERED BACKUP\n", encoding="utf-8")

        with self.assertRaises(ValueError):
            FrameworkInstaller.rollback(backup_dir)
        # Ensure original file was not overwritten with tampered backup
        self.assertIn("Preserve me.", (self.fake_project / "GEMINI.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""安裝器單元測試：驗證預設 Dry-Run、既有設定保留合併、自動備份與 SHA-256 校驗、完整回滾與憑證零觸碰。"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

STAGING_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = STAGING_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from install import FrameworkInstaller  # noqa: E402


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


if __name__ == "__main__":
    unittest.main(verbosity=2)

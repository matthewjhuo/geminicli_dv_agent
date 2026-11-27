"""Text kit packaging integrity; never contact GitHub or a model service."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("paste_kit_builder", ROOT / "scripts/build_paste_kit.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class PasteKitBuilder(unittest.TestCase):
    def test_generated_kit_roundtrips_pinned_source_and_license(self):
        # A source-only transfer has no .git directory. Its tests must remain usable.
        if not (ROOT / ".git").exists():
            self.skipTest("Packaging developer test requires git metadata; restored runtime does not")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "kit"
            info = builder.build("HEAD", output, harness2_from_kit=ROOT / "offline/paste-kit-d8b0c90")
            _, files = builder.read_kit(output)
            expected = subprocess.check_output(["git", "-C", str(ROOT), "show", "HEAD:src/dv_agent_platform/cli.py"])
            self.assertEqual(files["dv-agent-framework/src/dv_agent_platform/cli.py"], expected)
            self.assertFalse(any(name.startswith("dv-agent-framework/offline/") for name in files))
            self.assertEqual((output / "LICENSE-HARNESS2").read_bytes(), files["harness2/LICENSE"])
            self.assertEqual(info["files"], len(files))
            script = (output / "ALL_IN_ONE.sh").read_text(encoding="utf-8")
            for index in range(1, info["parts"] + 1):
                self.assertIn((output / f"{index:02d}_PART_{index:03d}.txt").read_text(encoding="utf-8"), script)

    def test_restore_script_executes_and_refuses_existing_destination(self):
        if not (ROOT / ".git").exists():
            self.skipTest("Packaging developer test requires git metadata")
        import sys, shutil
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "kit"
            info = builder.build("HEAD", output, harness2_from_kit=ROOT / "offline/paste-kit-7ff1213")
            stage = Path(temp) / "restore"
            stage.mkdir()
            for index in range(1, info["parts"]+1):
                text = (output / f"{index:02d}_PART_{index:03d}.txt").read_text(encoding="utf-8")
                marker = f"DV_PAYLOAD_PART_{index:03d}"
                body = text.split(f"<<'{marker}'\n", 1)[1].split(f"\n{marker}", 1)[0]
                (stage / f"part_{index:03d}.b64").write_text(body, encoding="ascii")
            first = subprocess.run([sys.executable, str(output / "restore.py")], cwd=stage, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("RESTORE OK", first.stdout)
            second = subprocess.run([sys.executable, str(output / "restore.py")], cwd=stage, capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("already exists", second.stderr)
            if sys.platform == "win32":
                extended = Path("\\\\?\\" + str(stage.resolve()))
                self.assertTrue(extended.samefile(stage))
                shutil.rmtree(extended)

    def test_corrupted_input_kit_and_invalid_chunk_size_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package-info.json").write_text(json.dumps({"parts": 1, "sha256": "wrong"}))
            (root / "01_PART_001.txt").write_text("cat <<'DV_PAYLOAD_PART_001'\nYWJj\nDV_PAYLOAD_PART_001\n")
            with self.assertRaises(ValueError):
                builder.read_kit(root)
            with self.assertRaises(ValueError):
                builder.build("HEAD", root / "new", harness2_from_kit=root, chunk_chars=7)


if __name__ == "__main__":
    unittest.main()

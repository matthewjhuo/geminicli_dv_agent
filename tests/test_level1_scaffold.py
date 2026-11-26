"""
Level 1 Validation Suite — Standardized DV Assistant Scaffold
Validates:
1. Directory & file completeness in staging/dv-agent-framework (Global GEMINI.md, Project GEMINI.md, 8 DV Skills + references).
2. Strict Gemini CLI 0.68.0 SKILL.md YAML frontmatter schema compliance (`name`, `description`, no malformed delimiters).
3. Engineering security & portability invariants:
   - Zero hardcoded user/project absolute paths (/home/<user>, C:\\Users\\, /proj/specific, etc.).
   - Zero embedded credentials/API keys/tokens.
   - Untrusted-data prompt-injection firewall present in Global GEMINI.md and Project GEMINI.md.
   - Read-only default & explicit Perforce `p4 submit` prohibition present in Global GEMINI.md.
   - Explicit simulation & file-modification approval rules present.
4. Integration check with installed Gemini CLI (`gemini skills list --all`) using an isolated staging GEMINI_CLI_HOME (zero touch on production ~/.gemini).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

STAGING_ROOT = Path(__file__).resolve().parents[1]
GLOBAL_GEMINI_MD = STAGING_ROOT / "global" / "GEMINI.md"
PROJECT_TEMPLATE_ROOT = STAGING_ROOT / "project-template"
PROJECT_GEMINI_MD = PROJECT_TEMPLATE_ROOT / "GEMINI.md"
SKILLS_DIR = PROJECT_TEMPLATE_ROOT / ".gemini" / "skills"

EXPECTED_SKILLS = {
    "dv-testplan": "references/testplan_matrix_template.md",
    "dv-uvm": "references/uvm_review_checklist.md",
    "dv-sva": "references/sva_patterns.md",
    "dv-regression-debug": "references/triage_report_schema.md",
    "dv-coverage-closure": "references/coverage_hole_disposition.md",
    "dv-formal": "references/formal_signoff_checklist.md",
    "dv-clock-reset": "references/cdc_rdc_checklist.md",
    "dv-ral-register": "references/ral_verification_matrix.md",
}

FRONTMATTER_RE = re.compile(
    r"^\ufeff?---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n([\s\S]*))?$"
)

FORBIDDEN_PATH_PATTERNS = [
    re.compile(r"[A-Za-z]:\\Users\\", re.IGNORECASE),
    re.compile(r"/home/[a-zA-Z0-9._-]+/"),
    re.compile(r"/Users/[a-zA-Z0-9._-]+/"),
]

FORBIDDEN_SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    re.compile(r"sk-[0-9A-Za-z]{20,}"),
    re.compile(r"ghp_[0-9A-Za-z]{30,}"),
    re.compile(r"(?i)(api[_-]?key|password|secret|token)\s*[:=]\s*['\"][^'\"<$\s]{8,}['\"]"),
]


def parse_skill_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError("Missing valid YAML frontmatter block delimited by ---")
    raw_fm, body = match.group(1), match.group(2) or ""
    parsed: dict[str, str] = {}
    for line in raw_fm.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"Invalid frontmatter line: {line!r}")
        key, val = line.split(":", 1)
        parsed[key.strip()] = val.strip().strip("'\"")
    return parsed, body.strip()


class TestLevel1StandardizedDVAssistant(unittest.TestCase):
    def test_01_global_and_project_gemini_md_exist_and_enforce_policies(self) -> None:
        self.assertTrue(GLOBAL_GEMINI_MD.is_file(), f"Missing {GLOBAL_GEMINI_MD}")
        self.assertTrue(PROJECT_GEMINI_MD.is_file(), f"Missing {PROJECT_GEMINI_MD}")

        global_text = GLOBAL_GEMINI_MD.read_text(encoding="utf-8")
        project_text = PROJECT_GEMINI_MD.read_text(encoding="utf-8")

        # Mandatory safety & governance assertions in Global GEMINI.md
        self.assertIn("untrusted", global_text.lower())
        self.assertIn("read-only", global_text.lower())
        self.assertIn("p4 submit", global_text)
        self.assertIn("NEVER", global_text)
        self.assertIn("[CONFIRMED]", global_text)
        self.assertIn("[HYPOTHESIS]", global_text)

        # Project template parameterization assertions
        self.assertIn("$DV_PROJECT_ROOT", project_text)
        self.assertIn("[APPROVAL REQUIRED]", project_text)
        self.assertIn("untrusted", project_text.lower())

    def test_02_all_eight_dv_skills_and_references_valid(self) -> None:
        self.assertTrue(SKILLS_DIR.is_dir(), f"Missing skills dir {SKILLS_DIR}")
        discovered_dirs = {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()}
        self.assertEqual(
            discovered_dirs,
            set(EXPECTED_SKILLS.keys()),
            f"Expected exactly 8 DV skills, got {discovered_dirs}",
        )

        for skill_name, rel_ref in EXPECTED_SKILLS.items():
            skill_md = SKILLS_DIR / skill_name / "SKILL.md"
            ref_md = SKILLS_DIR / skill_name / rel_ref
            self.assertTrue(skill_md.is_file(), f"Missing {skill_md}")
            self.assertTrue(ref_md.is_file(), f"Missing reference {ref_md}")

            content = skill_md.read_text(encoding="utf-8")
            fm, body = parse_skill_frontmatter(content)
            self.assertEqual(fm.get("name"), skill_name)
            self.assertTrue(
                len(fm.get("description", "")) >= 40,
                f"Skill {skill_name} description too short for reliable autonomous activation",
            )
            self.assertIn(rel_ref, body, f"Skill {skill_name} should link to {rel_ref}")
            self.assertGreater(len(body), 600, f"Skill {skill_name} body unexpectedly thin")
            self.assertIn("untrusted", body.lower(), f"Skill {skill_name} missing untrusted-data reminder")

    def test_03_zero_hardcoded_paths_and_zero_secrets_across_all_files(self) -> None:
        checked_count = 0
        for path in STAGING_ROOT.rglob("*"):
            if (
                not path.is_file()
                or path.suffix == ".pyc"
                or "__pycache__" in path.parts
                or ".git" in path.parts
                or ".venv" in path.parts
                or ".dv_agent" in path.parts
                or "runs" in path.parts
            ):
                continue
            if path.resolve() == Path(__file__).resolve():
                continue
            text = path.read_text(encoding="utf-8")
            checked_count += 1
            for pat in FORBIDDEN_PATH_PATTERNS:
                self.assertIsNone(
                    pat.search(text),
                    f"Hardcoded user/system path matched {pat.pattern!r} in {path}",
                )
            for spat in FORBIDDEN_SECRET_PATTERNS:
                self.assertIsNone(
                    spat.search(text),
                    f"Potential embedded secret matched {spat.pattern!r} in {path}",
                )
        self.assertGreaterEqual(checked_count, 18)

    def test_04_gemini_cli_native_skill_discovery(self) -> None:
        gemini_bin = shutil.which("gemini")
        if not gemini_bin:
            self.skipTest("gemini CLI binary not in PATH")

        with tempfile.TemporaryDirectory(prefix="dv_agent_l1_cli_") as isolated_home:
            # Populate isolated GEMINI_CLI_HOME/.gemini/GEMINI.md and an isolated project root
            # (detached from any parent .git repository so Gemini CLI discovers workspace skills reliably).
            isolated_dot_gemini = Path(isolated_home) / ".gemini"
            isolated_dot_gemini.mkdir(parents=True, exist_ok=True)
            shutil.copy2(GLOBAL_GEMINI_MD, isolated_dot_gemini / "GEMINI.md")

            isolated_project = Path(isolated_home) / "sample_dv_project"
            shutil.copytree(PROJECT_TEMPLATE_ROOT, isolated_project)
            (isolated_dot_gemini / "trustedFolders.json").write_text(
                json.dumps({str(isolated_project.resolve()): "TRUST_FOLDER"}),
                encoding="utf-8",
            )

            env = os.environ.copy()
            env["GEMINI_CLI_HOME"] = isolated_home
            proc = subprocess.run(
                [gemini_bin, "skills", "list", "--all"],
                cwd=str(isolated_project),
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            combined_out = f"{proc.stdout}\n{proc.stderr}"
            self.assertEqual(
                proc.returncode,
                0,
                f"gemini skills list failed (rc={proc.returncode}):\n{combined_out}",
            )
            for skill_name in EXPECTED_SKILLS:
                self.assertIn(
                    skill_name,
                    combined_out,
                    f"Gemini CLI did not discover skill {skill_name!r}. Output:\n{combined_out}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)

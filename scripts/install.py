#!/usr/bin/env python3
"""Non-destructive installer & rollback utility for the Agentic DV Framework.

Features:
* Defaults to ``--dry-run`` (zero filesystem writes unless ``--apply`` is passed).
* Preserves existing ``GEMINI.md`` via idempotent fenced section merge.
* Preserves existing ``settings.json`` keys via additive deep-merge (UTF-8 without BOM).
* Creates timestamped backups with SHA-256 checksums before modifying any existing file.
* Never reads, copies, or touches credential files (oauth_creds.json, gemini-credentials.json, etc.).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

FRAMEWORK_ROOT = Path(__file__).resolve().parents[1]
BEGIN_MARKER = "<!-- BEGIN AGENTIC_DV_FRAMEWORK -->"
END_MARKER = "<!-- END AGENTIC_DV_FRAMEWORK -->"

FORBIDDEN_CREDENTIAL_FILENAMES = {
    "gemini-credentials.json",
    "google_accounts.json",
    "google_account_id",
    "oauth_creds.json",
    ".env",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def merge_markdown_section(existing: str, addition: str) -> str:
    block = f"{BEGIN_MARKER}\n{addition.strip()}\n{END_MARKER}\n"
    if BEGIN_MARKER in existing and END_MARKER in existing:
        pre, _, rest = existing.partition(BEGIN_MARKER)
        _, _, post = rest.partition(END_MARKER)
        return pre.rstrip() + "\n\n" + block + post.lstrip()
    if not existing.strip():
        return block
    return existing.rstrip() + "\n\n" + block


def merge_settings_json(existing: Dict[str, Any], template: Dict[str, Any]) -> Dict[str, Any]:
    out = json.loads(json.dumps(existing))
    for k, v in template.items():
        if k not in out:
            out[k] = v
        elif k == "mcpServers" and isinstance(out[k], dict) and isinstance(v, dict):
            for srv_name, srv_cfg in v.items():
                # Preserve existing server definition if user already customized it
                out[k].setdefault(srv_name, srv_cfg)
        elif k == "hooks" and isinstance(out[k], dict) and isinstance(v, dict):
            for event_name, hook_groups in v.items():
                cur_groups = out[k].setdefault(event_name, [])
                existing_names = {
                    h.get("name")
                    for grp in cur_groups if isinstance(grp, dict)
                    for h in (grp.get("hooks") or []) if isinstance(h, dict)
                }
                for grp in hook_groups:
                    grp_names = {h.get("name") for h in (grp.get("hooks") or []) if isinstance(h, dict)}
                    if not (grp_names & existing_names):
                        cur_groups.append(grp)
        elif isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = merge_settings_json(out[k], v)
    return out


class FrameworkInstaller:
    def __init__(self, framework_root: Path = FRAMEWORK_ROOT, apply: bool = False):
        self.framework_root = Path(framework_root).resolve()
        self.apply = apply
        self.actions: List[Dict[str, Any]] = []

    def _backup_file(self, target: Path, backup_dir: Path, manifest: List[Dict[str, str]]) -> None:
        if not target.exists() or target.name in FORBIDDEN_CREDENTIAL_FILENAMES:
            return
        rel_name = target.name + "." + hashlib.md5(str(target).encode("utf-8")).hexdigest()[:8]
        bpath = backup_dir / rel_name
        if self.apply:
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, bpath)
            manifest.append({"original": str(target), "backup": str(bpath), "sha256": _sha256(target)})

    def _write_text(self, target: Path, content: str, backup_dir: Path, manifest: List[Dict[str, str]],
                    executable: bool = False) -> None:
        existed = target.exists()
        if existed and target.read_text(encoding="utf-8") == content:
            self.actions.append({"op": "unchanged", "target": str(target)})
            return
        if existed:
            self._backup_file(target, backup_dir, manifest)
        self.actions.append({"op": "update" if existed else "create", "target": str(target), "dry_run": not self.apply})
        if self.apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Always write UTF-8 WITHOUT BOM for Gemini CLI compatibility
            target.write_text(content, encoding="utf-8", newline="\n")
            if executable:
                mode = target.stat().st_mode
                target.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def install_global(self, user_gemini_dir: Path) -> Dict[str, Any]:
        user_gemini_dir = Path(user_gemini_dir).resolve()
        ts = time.strftime("%Y%m%d-%H%M%S")
        backup_dir = user_gemini_dir / ".dv_agent_backups" / f"global-{ts}"
        manifest: List[Dict[str, str]] = []

        # 1. Merge global GEMINI.md without overwriting user notes
        src_gmd = (self.framework_root / "global" / "GEMINI.md").read_text(encoding="utf-8")
        dst_gmd = user_gemini_dir / "GEMINI.md"
        existing_gmd = dst_gmd.read_text(encoding="utf-8") if dst_gmd.is_file() else ""
        merged_gmd = merge_markdown_section(existing_gmd, src_gmd)
        self._write_text(dst_gmd, merged_gmd, backup_dir, manifest)

        # 2. Additive merge into ~/.gemini/settings.json
        src_settings = json.loads((self.framework_root / "global" / "settings.template.json").read_text(encoding="utf-8"))
        dst_settings = user_gemini_dir / "settings.json"
        existing_settings = json.loads(dst_settings.read_text(encoding="utf-8")) if dst_settings.is_file() else {}
        merged_settings = merge_settings_json(existing_settings, src_settings)
        self._write_text(dst_settings, json.dumps(merged_settings, indent=2, ensure_ascii=False) + "\n", backup_dir, manifest)

        if self.apply and manifest:
            (backup_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"scope": "global", "dry_run": not self.apply, "backup_dir": str(backup_dir), "actions": list(self.actions)}

    def install_project(self, project_root: Path) -> Dict[str, Any]:
        project_root = Path(project_root).resolve()
        ts = time.strftime("%Y%m%d-%H%M%S")
        backup_dir = project_root / ".dv_agent" / "backups" / f"project-{ts}"
        manifest: List[Dict[str, str]] = []
        tpl_root = self.framework_root / "project-template"

        for src in sorted(tpl_root.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(tpl_root)
            dst = project_root / rel
            src_text = src.read_text(encoding="utf-8")

            if rel.as_posix() == "GEMINI.md" and dst.is_file():
                merged = merge_markdown_section(dst.read_text(encoding="utf-8"), src_text)
                self._write_text(dst, merged, backup_dir, manifest)
            elif rel.as_posix() == ".gemini/settings.json" and dst.is_file():
                existing = json.loads(dst.read_text(encoding="utf-8"))
                merged_obj = merge_settings_json(existing, json.loads(src_text))
                self._write_text(dst, json.dumps(merged_obj, indent=2, ensure_ascii=False) + "\n", backup_dir, manifest)
            else:
                is_hook = rel.as_posix().startswith(".gemini/hooks/") and src.suffix == ".py"
                self._write_text(dst, src_text, backup_dir, manifest, executable=is_hook)

        if self.apply and manifest:
            (backup_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"scope": "project", "dry_run": not self.apply, "backup_dir": str(backup_dir), "actions": list(self.actions)}

    @staticmethod
    def rollback(backup_dir: Path) -> List[str]:
        manifest_file = Path(backup_dir) / "manifest.json"
        if not manifest_file.is_file():
            raise FileNotFoundError(f"Backup manifest not found: {manifest_file}")
        entries = json.loads(manifest_file.read_text(encoding="utf-8"))
        restored: List[str] = []
        for item in entries:
            shutil.copy2(item["backup"], item["original"])
            restored.append(item["original"])
        return restored


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Non-destructive installer for Agentic DV Framework")
    parser.add_argument("--project-dir", help="Target DV project workspace directory")
    parser.add_argument("--global-gemini-dir", help="Target user ~/.gemini directory (requires explicit user approval)")
    parser.add_argument("--rollback", help="Restore files from a backup directory containing manifest.json")
    parser.add_argument("--apply", action="store_true", help="Write changes (with backup); defaults to dry-run preview")
    args = parser.parse_args(argv)

    if args.rollback:
        restored = FrameworkInstaller.rollback(Path(args.rollback))
        print(json.dumps({"restored": restored}, indent=2, ensure_ascii=False))
        return 0

    if not args.project_dir and not args.global_gemini_dir:
        parser.error("Specify --project-dir and/or --global-gemini-dir (or --rollback)")

    reports = []
    if args.global_gemini_dir:
        inst = FrameworkInstaller(apply=args.apply)
        reports.append(inst.install_global(Path(args.global_gemini_dir)))
    if args.project_dir:
        inst = FrameworkInstaller(apply=args.apply)
        reports.append(inst.install_project(Path(args.project_dir)))

    print(json.dumps(reports, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

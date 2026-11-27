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
import re
import shutil
import stat
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

FRAMEWORK_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = FRAMEWORK_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dv_agent_platform.errors import ConfigError  # noqa: E402
from dv_agent_platform.jsonc import loads as load_jsonc  # noqa: E402
from dv_agent_platform.templates import (  # noqa: E402
    CompanyProfile,
    RenderSafetyError,
    load_company_profile,
    render_env_sh_example,
    render_project_bundle,
    render_vscode_settings,
    validate_rendered_content,
)

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
    def __init__(
        self,
        framework_root: Path = FRAMEWORK_ROOT,
        apply: bool = False,
        *,
        profile_path: Optional[Path] = None,
        strict_render: bool = False,
        emit_vscode: bool = False,
        emit_env_example: bool = False,
        replace_customized: bool = False,
        refresh_framework_settings: bool = False,
    ):
        self.framework_root = Path(framework_root).resolve()
        self.apply = apply
        self.profile_path = Path(profile_path) if profile_path is not None else None
        self.strict_render = strict_render
        self.emit_vscode = emit_vscode
        self.emit_env_example = emit_env_example
        self.replace_customized = replace_customized
        self.refresh_framework_settings = refresh_framework_settings
        self.warnings: List[str] = []
        self.actions: List[Dict[str, Any]] = []

    def _backup_file(self, target: Path, backup_dir: Path, manifest: List[Dict[str, str]]) -> None:
        if not target.exists() or target.name in FORBIDDEN_CREDENTIAL_FILENAMES:
            return
        rel_name = target.name + "." + hashlib.md5(str(target).encode("utf-8")).hexdigest()[:8]
        bpath = backup_dir / rel_name
        if self.apply:
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, bpath)
            manifest.append(
                {
                    "op": "update",
                    "original": str(target),
                    "backup": str(bpath),
                    "sha256": _sha256(target),
                }
            )

    def _write_text(
        self,
        target: Path,
        content: str,
        backup_dir: Path,
        manifest: List[Dict[str, str]],
        executable: bool = False,
    ) -> None:
        if target.name in FORBIDDEN_CREDENTIAL_FILENAMES:
            raise PermissionError(f"Refusing to write forbidden credential file: {target.name}")
        existed = target.exists()
        if existed and target.read_text(encoding="utf-8") == content:
            self.actions.append({"op": "unchanged", "target": str(target)})
            return
        if existed:
            self._backup_file(target, backup_dir, manifest)
        elif self.apply:
            manifest.append({"op": "create", "original": str(target)})
        self.actions.append(
            {"op": "update" if existed else "create", "target": str(target), "dry_run": not self.apply}
        )
        if self.apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Always write UTF-8 WITHOUT BOM for Gemini CLI compatibility
            target.write_text(content, encoding="utf-8", newline="\n")
            if executable:
                mode = target.stat().st_mode
                target.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def install_global(self, user_gemini_dir: Path) -> Dict[str, Any]:
        user_gemini_dir = Path(user_gemini_dir).resolve()
        ts = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns()}"
        backup_dir = user_gemini_dir / ".dv_agent_backups" / f"global-{ts}"
        manifest: List[Dict[str, str]] = []

        # 1. Merge global GEMINI.md without overwriting user notes
        src_gmd = (self.framework_root / "global" / "GEMINI.md").read_text(encoding="utf-8")
        dst_gmd = user_gemini_dir / "GEMINI.md"
        existing_gmd = dst_gmd.read_text(encoding="utf-8") if dst_gmd.is_file() else ""
        merged_gmd = merge_markdown_section(existing_gmd, src_gmd)
        validate_rendered_content(
            merged_gmd, filename="global/GEMINI.md", strict=False, check_placeholders=False
        )

        # 2. Additive merge into ~/.gemini/settings.json
        src_settings = json.loads(
            (self.framework_root / "global" / "settings.template.json").read_text(encoding="utf-8")
        )
        dst_settings = user_gemini_dir / "settings.json"
        existing_settings = json.loads(dst_settings.read_text(encoding="utf-8")) if dst_settings.is_file() else {}
        merged_settings = merge_settings_json(existing_settings, src_settings)
        settings_text = json.dumps(merged_settings, indent=2, ensure_ascii=False) + "\n"
        validate_rendered_content(
            settings_text, filename="global/settings.json", strict=False, check_placeholders=False
        )

        self._write_text(dst_gmd, merged_gmd, backup_dir, manifest)
        self._write_text(dst_settings, settings_text, backup_dir, manifest)

        if self.apply and manifest:
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / "manifest.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        return {
            "scope": "global",
            "dry_run": not self.apply,
            "backup_dir": str(backup_dir),
            "actions": list(self.actions),
        }

    def install_project(
        self,
        project_root: Path,
        *,
        profile_path: Optional[Path] = None,
        strict_render: Optional[bool] = None,
        emit_vscode: Optional[bool] = None,
        emit_env_example: Optional[bool] = None,
    ) -> Dict[str, Any]:
        project_root = Path(project_root).resolve()
        ts = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns()}"
        backup_dir = project_root / ".dv_agent" / "backups" / f"project-{ts}"
        manifest: List[Dict[str, str]] = []
        tpl_root = self.framework_root / "project-template"
        install_state_path = project_root / ".dv_agent" / "install-state.json"
        installed_hashes = (json.loads(install_state_path.read_text(encoding="utf-8"))
                            if install_state_path.is_file() else {})
        next_hashes = dict(installed_hashes)

        eff_profile_path = Path(profile_path) if profile_path is not None else self.profile_path
        eff_strict = self.strict_render if strict_render is None else strict_render
        eff_emit_vscode = self.emit_vscode if emit_vscode is None else emit_vscode
        eff_emit_env = self.emit_env_example if emit_env_example is None else emit_env_example

        profile: Optional[CompanyProfile] = None
        bundle: Dict[str, str] = {}
        if eff_profile_path is not None:
            profile = load_company_profile(eff_profile_path)
            bundle = render_project_bundle(
                profile,
                tpl_root,
                strict=eff_strict,
                emit_vscode=eff_emit_vscode,
                emit_env_example=eff_emit_env,
            )

        # Phase 1: Render and validate ALL planned writes in memory before touching disk
        planned_writes: List[Tuple[Path, str, bool]] = []

        for src in sorted(tpl_root.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(tpl_root)
            rel_posix = rel.as_posix()
            dst = project_root / rel
            src_text = bundle[rel_posix] if rel_posix in bundle else src.read_text(encoding="utf-8")

            if rel_posix == "GEMINI.md":
                # Validate rendered framework block strictly before merging pre-existing user prose
                validate_rendered_content(
                    src_text,
                    filename="GEMINI.md",
                    strict=eff_strict,
                    check_placeholders=True,
                )
                if dst.is_file():
                    final_text = merge_markdown_section(dst.read_text(encoding="utf-8"), src_text)
                else:
                    final_text = merge_markdown_section("", src_text)
                validate_rendered_content(
                    final_text,
                    filename="GEMINI.md",
                    strict=False,
                    check_placeholders=False,
                )
                planned_writes.append((dst, final_text, False))
            elif rel_posix == ".gemini/settings.json":
                tpl_obj = json.loads(src_text)
                if dst.is_file():
                    existing = json.loads(dst.read_text(encoding="utf-8"))
                    merged_obj = merge_settings_json(existing, tpl_obj)
                    for name, cfg in tpl_obj.get("mcpServers", {}).items():
                        old = existing.get("mcpServers", {}).get(name)
                        if old is not None and old != cfg:
                            if self.refresh_framework_settings:
                                merged_obj["mcpServers"][name] = cfg
                            else:
                                self.warnings.append(f"Preserved existing MCP server {name}; use --refresh-framework-settings to refresh it.")
                    for event, groups in tpl_obj.get("hooks", {}).items():
                        framework_names = {h.get("name") for g in groups for h in g.get("hooks", [])}
                        current_groups = merged_obj.get("hooks", {}).get(event, [])
                        if self.refresh_framework_settings:
                            kept = []
                            for group in current_groups:
                                kept_hooks = [h for h in group.get("hooks", []) if h.get("name") not in framework_names]
                                if kept_hooks:
                                    kept.append({**group, "hooks": kept_hooks})
                            merged_obj.setdefault("hooks", {})[event] = kept + groups
                        elif any(h.get("name") in framework_names for g in existing.get("hooks", {}).get(event, [])
                                 for h in g.get("hooks", [])):
                            self.warnings.append(f"Preserved existing {event} framework hooks; review --refresh-framework-settings when upgrading.")
                    final_text = json.dumps(merged_obj, indent=2, ensure_ascii=False) + "\n"
                else:
                    final_text = json.dumps(tpl_obj, indent=2, ensure_ascii=False) + "\n"
                validate_rendered_content(
                    final_text,
                    filename=rel_posix,
                    strict=eff_strict,
                    check_placeholders=True,
                )
                planned_writes.append((dst, final_text, False))
            else:
                validate_rendered_content(
                    src_text,
                    filename=rel_posix,
                    strict=eff_strict,
                    check_placeholders=False,
                )
                is_hook = rel_posix.startswith(".gemini/hooks/") and src.suffix == ".py"
                planned_writes.append((dst, src_text, is_hook))

        if profile is not None:
            dv_proj_text = bundle["dv_project.json"]
            validate_rendered_content(
                dv_proj_text,
                filename="dv_project.json",
                strict=eff_strict,
                check_placeholders=True,
            )
            planned_writes.append((project_root / "dv_project.json", dv_proj_text, False))

        eff_profile = profile
        if eff_profile is None and (eff_emit_env or eff_emit_vscode):
            default_id = re.sub(r"[^A-Za-z0-9._-]", "_", project_root.name) or "dv_project"
            eff_profile = CompanyProfile(project={"project_id": default_id})

        if eff_emit_env and eff_profile is not None:
            env_text = bundle.get(".dv_agent/env.sh.example") or render_env_sh_example(eff_profile)
            validate_rendered_content(
                env_text,
                filename=".dv_agent/env.sh.example",
                strict=eff_strict,
                check_placeholders=True,
            )
            planned_writes.append((project_root / ".dv_agent" / "env.sh.example", env_text, False))

        if eff_emit_vscode and eff_profile is not None:
            vscode_dst = project_root / ".vscode" / "settings.json"
            vscode_tpl = (
                json.loads(bundle[".vscode/settings.json"])
                if ".vscode/settings.json" in bundle
                else render_vscode_settings(eff_profile)
            )
            if vscode_dst.is_file():
                try:
                    existing_vscode = load_jsonc(vscode_dst.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    raise ConfigError(f"Invalid JSON in existing {vscode_dst}: {exc}") from exc
                merged_vscode = merge_settings_json(existing_vscode, vscode_tpl)
            else:
                merged_vscode = vscode_tpl
            vscode_text = json.dumps(merged_vscode, indent=2, ensure_ascii=False) + "\n"
            validate_rendered_content(
                vscode_text,
                filename=".vscode/settings.json",
                strict=eff_strict,
                check_placeholders=True,
            )
            planned_writes.append((vscode_dst, vscode_text, False))

        # Phase 2: All validations passed; execute backups and writes
        for dst, content, is_hook in planned_writes:
            rel = dst.relative_to(project_root).as_posix()
            # Markdown/settings have explicit merge rules. Preserve customized
            # generated assets unless replacement was explicitly requested.
            merged = rel in {"GEMINI.md", ".gemini/settings.json", ".vscode/settings.json"}
            desired_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if dst.is_file() and not merged and not self.replace_customized:
                current_hash = _sha256(dst)
                if current_hash != desired_hash and installed_hashes.get(rel) != current_hash:
                    self.warnings.append(f"Preserved customized file: {rel}; use --replace-customized to replace with backup.")
                    self.actions.append({"op": "preserve_customized", "target": str(dst)})
                    continue
            self._write_text(dst, content, backup_dir, manifest, executable=is_hook)
            next_hashes[rel] = desired_hash

        self._write_text(install_state_path, json.dumps(next_hashes, indent=2) + "\n", backup_dir, manifest)

        if self.apply and manifest:
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / "manifest.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        return {
            "scope": "project",
            "warnings": self.warnings,
            "dry_run": not self.apply,
            "backup_dir": str(backup_dir),
            "actions": list(self.actions),
        }

    @staticmethod
    def rollback(backup_dir: Path) -> List[str]:
        manifest_file = Path(backup_dir) / "manifest.json"
        if not manifest_file.is_file():
            raise FileNotFoundError(f"Backup manifest not found: {manifest_file}")
        entries = json.loads(manifest_file.read_text(encoding="utf-8"))
        # Verify all backup checksums before mutating any target file
        for item in entries:
            if "backup" in item:
                bpath = Path(item["backup"])
                if not bpath.is_file():
                    raise FileNotFoundError(f"Backup file missing: {bpath}")
                expected_sha = item.get("sha256")
                if expected_sha:
                    actual_sha = _sha256(bpath)
                    if actual_sha != expected_sha:
                        raise ValueError(
                            f"SHA-256 mismatch for backup {bpath}: expected {expected_sha}, got {actual_sha}"
                        )

        backup_path = Path(backup_dir).resolve()
        restored: List[str] = []
        for item in reversed(entries):
            orig = Path(item["original"])
            if item.get("op") == "create" and "backup" not in item:
                if orig.is_file():
                    orig.unlink()
                    restored.append(str(orig))
                    # Prune empty directories created for this file without leaving backup root
                    parent = orig.parent
                    while (
                        parent != parent.parent
                        and parent not in backup_path.parents
                        and backup_path not in parent.parents
                        and parent != backup_path
                    ):
                        try:
                            parent.rmdir()
                        except OSError:
                            break
                        parent = parent.parent
            elif "backup" in item:
                orig.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item["backup"], orig)
                restored.append(str(orig))
        return restored


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Non-destructive installer for Agentic DV Framework")
    parser.add_argument("--project-dir", help="Target DV project workspace directory")
    parser.add_argument("--global-gemini-dir", help="Target user ~/.gemini directory (requires explicit user approval)")
    parser.add_argument("--rollback", help="Restore files from a backup directory containing manifest.json")
    mode_grp = parser.add_mutually_exclusive_group()
    mode_grp.add_argument("--apply", action="store_true", help="Write changes (with backup); defaults to dry-run preview")
    mode_grp.add_argument("--dry-run", action="store_true", help="Preview changes without modifying target workspace (default)")
    scope_grp = parser.add_mutually_exclusive_group()
    scope_grp.add_argument("--global-only", action="store_true", help="Only install global ~/.gemini configuration")
    scope_grp.add_argument("--project-only", action="store_true", help="Only install project workspace configuration")
    parser.add_argument("--profile", help="Path to company testbench profile JSON")
    parser.add_argument("--replace-customized", action="store_true", help="Replace customized framework assets with backup")
    parser.add_argument("--refresh-framework-settings", action="store_true", help="Refresh same-name framework MCP servers with backup")
    parser.add_argument(
        "--strict-render",
        action="store_true",
        help="Reject unresolved <...> or {{...}} placeholders in rendered output",
    )
    parser.add_argument("--emit-vscode", action="store_true", help="Emit/merge non-secret .vscode/settings.json")
    parser.add_argument(
        "--emit-env-example",
        action="store_true",
        help="Emit non-secret .dv_agent/env.sh.example",
    )
    args = parser.parse_args(argv)

    try:
        if args.rollback:
            restored = FrameworkInstaller.rollback(Path(args.rollback))
            print(json.dumps({"restored": restored}, indent=2, ensure_ascii=False))
            return 0

        if args.global_only and not args.global_gemini_dir:
            parser.error("--global-only requires --global-gemini-dir")
        if args.project_only and not args.project_dir:
            parser.error("--project-only requires --project-dir")
        if not args.project_dir and not args.global_gemini_dir:
            parser.error("Specify --project-dir and/or --global-gemini-dir (or --rollback)")

        apply_mode = bool(args.apply) and not bool(args.dry_run)
        profile_path = Path(args.profile) if args.profile else None

        reports = []
        if args.global_gemini_dir and not args.project_only:
            inst_g = FrameworkInstaller(apply=apply_mode)
            reports.append(inst_g.install_global(Path(args.global_gemini_dir)))
        if args.project_dir and not args.global_only:
            inst_p = FrameworkInstaller(
                apply=apply_mode,
                profile_path=profile_path,
                strict_render=args.strict_render,
                emit_vscode=args.emit_vscode,
                emit_env_example=args.emit_env_example,
                replace_customized=args.replace_customized,
                refresh_framework_settings=args.refresh_framework_settings,
            )
            reports.append(inst_p.install_project(Path(args.project_dir)))

        print(json.dumps(reports, indent=2, ensure_ascii=False))
        return 0
    except (RenderSafetyError, ConfigError, FileNotFoundError, PermissionError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

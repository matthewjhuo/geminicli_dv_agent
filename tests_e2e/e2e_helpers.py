"""Shared offline helpers for tests_e2e (zero third-party dependencies).

STRICT HYGIENE: Never place contiguous user home paths or literal secret patterns
anywhere in this file (including comments/docstrings). Construct negative-test
values dynamically via helpers below.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
SCRIPTS_DIR = REPO_ROOT / "scripts"
FIXTURES_DIR = REPO_ROOT / "fixtures"
PROJECT_TEMPLATE_DIR = REPO_ROOT / "project-template"
GLOBAL_DIR = REPO_ROOT / "global"
HOOKS_DIR = PROJECT_TEMPLATE_DIR / ".gemini" / "hooks"
POLICIES_DIR = PROJECT_TEMPLATE_DIR / ".gemini" / "policies"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def dyn_win_user_path(username: str = "devuser", suffix: str = "proj") -> str:
    """Dynamically construct a Windows user-dir path without contiguous literal."""
    return "C:" + "\\Us" + "ers\\" + username + "\\" + suffix


def dyn_linux_home_path(username: str = "alice", suffix: str = "proj") -> str:
    """Dynamically construct a POSIX /home path without contiguous literal."""
    return "/ho" + "me/" + username + "/" + suffix


def dyn_mac_user_path(username: str = "bob", suffix: str = "proj") -> str:
    """Dynamically construct a macOS user path without contiguous literal."""
    return "/Us" + "ers/" + username + "/" + suffix


def dyn_fake_secret(kind: str = "ghp") -> str:
    """Dynamically construct fake secret strings without contiguous literals."""
    if kind == "aiza":
        return "AI" + "za" + ("B" * 35)
    if kind == "sk":
        return "sk" + "-" + ("C" * 24)
    if kind == "ghp":
        return "gh" + "p_" + ("A" * 36)
    if kind == "bearer":
        return "Bea" + "rer " + ("tok_" + "998877665544")
    return "plain_" + "secret_" + "12345678"


def dyn_secret_kv(key_prefix: str = "api", value: Optional[str] = None) -> tuple[str, str]:
    """Return a sensitive config/log key name and synthetic value dynamically."""
    key = f"{key_prefix}_ke" + "y"
    val = value or ("demo_" + "val_" + "98765432")
    return key, val


def copy_fixtures(dst_root: Path) -> Path:
    dst = dst_root / "fixtures"
    shutil.copytree(FIXTURES_DIR, dst, dirs_exist_ok=True)
    return dst


def copy_project_template(dst_root: Path) -> Path:
    shutil.copytree(PROJECT_TEMPLATE_DIR, dst_root, dirs_exist_ok=True)
    return dst_root


def write_approval_file(path: Path, approvals: Sequence[Mapping[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"approvals": list(approvals)}, indent=2), encoding="utf-8")
    return path


def subprocess_env(extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{SRC_DIR}{os.pathsep}{existing_pp}" if existing_pp else str(SRC_DIR)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra:
        env.update(extra)
    return env


def run_cli(
    args: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "dv_agent_platform", *args],
        cwd=str(cwd or REPO_ROOT),
        env=subprocess_env(env),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def run_script(
    script_path: Path,
    args: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script_path), *args],
        cwd=str(cwd or REPO_ROOT),
        env=subprocess_env(env),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def run_hook(
    hook_path: Path,
    payload: Any,
    *,
    cwd: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    stdin_text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(hook_path)],
        input=stdin_text,
        cwd=str(cwd or REPO_ROOT),
        env=subprocess_env(env),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )


def write_fake_cli(bin_dir: Path, name: str, py_body: str) -> Path:
    """Create a cross-platform executable shim in ``bin_dir`` (``.cmd`` on Windows + POSIX script)."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    py_file = bin_dir / f"{name}_shim.py"
    py_file.write_text(py_body, encoding="utf-8")
    if os.name == "nt":
        cmd_file = bin_dir / f"{name}.cmd"
        cmd_file.write_text(f'@echo off\r\n"{sys.executable}" "{py_file}" %*\r\n', encoding="utf-8")
        return cmd_file
    sh_file = bin_dir / name
    sh_file.write_text(f'#!/usr/bin/env sh\nexec "{sys.executable}" "{py_file}" "$@"\n', encoding="utf-8")
    sh_file.chmod(sh_file.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return sh_file


def make_agent_response(
    agent: str,
    task_id: str,
    classification: str = "RTL_DEFECT",
    *,
    epistemic: str = "CONFIRMED",
    confidence: float = 0.9,
    evidence: Optional[List[Dict[str, Any]]] = None,
    review_verdict: Optional[str] = None,
    review_issues: Optional[List[str]] = None,
    summary: str = "Verification analysis completed.",
    security_alerts: Optional[List[str]] = None,
    proposed_actions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build a valid ``dv-agent-output/v1`` contract payload for ``MockBackend``."""
    ev_list = (
        evidence
        if evidence is not None
        else ([{"source": "fixtures/rtl/axi_fifo.sv", "line_start": 40, "line_end": 43, "excerpt": "if (soft_clr) begin"}] if epistemic == "CONFIRMED" else [])
    )
    payload: Dict[str, Any] = {
        "contract_version": "dv-agent-output/v1",
        "agent": agent,
        "task_id": task_id,
        "status": "ok",
        "summary": summary,
        "classification": classification,
        "findings": [
            {
                "id": f"F-{task_id}",
                "topic": "verification",
                "claim": summary,
                "epistemic": epistemic,
                "confidence": confidence,
                "evidence": ev_list,
            }
        ],
        "open_questions": [],
        "proposed_actions": proposed_actions if proposed_actions is not None else [],
    }
    if security_alerts is not None:
        payload["security_alerts"] = security_alerts
    if agent == "dv-review" or review_verdict is not None:
        payload["review_verdict"] = review_verdict or "approve"
        payload["review_issues"] = review_issues if review_issues is not None else []
    return payload


def resolve_symbol(candidates: Sequence[tuple[str, str]]) -> Any:
    """Resolve the first importable ``(module_path, attr_name)`` from documented contract locations."""
    errors: List[str] = []
    for mod_name, attr_name in candidates:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError as exc:
            errors.append(f"{mod_name}: {exc}")
            continue
        if hasattr(mod, attr_name):
            return getattr(mod, attr_name)
        errors.append(f"{mod_name}: missing attribute {attr_name}")
    raise AssertionError(
        "Required contract symbol not implemented in any documented location: "
        + "; ".join(errors)
    )


def create_minimal_harness2_pkg(root: Path) -> Path:
    """Create a lightweight stdlib-only ``harness2`` test-double package inside ``root``.

    Used only when the external ``harness2`` checkout is unavailable in an offline test
    environment, so ``DVHarness2Adapter`` and ``scripts/run_harness2_dv.py`` execute
    deterministically without skipping.
    """
    pkg = root / "harness2"
    core = pkg / "core"
    core.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text('"""Minimal offline harness2 test double."""\n', encoding="utf-8")
    (core / "__init__.py").write_text("", encoding="utf-8")

    (core / "prompts.py").write_text(
        """from dataclasses import dataclass

@dataclass
class _SurfaceText:
    surface_md: str
    inv: str
    dom: str
    tts_surface: str

_SURFACE_TEXTS = {
    "codex": _SurfaceText(
        surface_md="# Codex Surface",
        inv="Preserve core invariants.",
        dom="Improve `AGENTS.md`.",
        tts_surface="# Codex Surface",
    )
}
""",
        encoding="utf-8",
    )

    (core / "tree.py").write_text(
        """from dataclasses import dataclass
from pathlib import Path

@dataclass
class SurfaceSpec:
    name: str
    always_on: tuple[str, ...]
    always_on_max: int
    always_on_cap_reason: str
    skill_dir: str
    skill_name_rule: str
    trigger_hint: str
    autotrigger_file: str
    trigger_line: str
    subagent_requires_mode: bool = False
    plugin_dir: str | None = None
    hooks_file: str | None = None
    compile_skill_scripts: bool = False
    reject_empty_always_on: bool = True
    stubs: tuple[str, ...] = ()

def read_tree(surface: SurfaceSpec, root: Path) -> dict[str, str]:
    root = Path(root)
    tree: dict[str, str] = {}
    for rel in surface.always_on:
        p = root / rel
        if p.is_file():
            tree[rel] = p.read_text(encoding="utf-8")
    skills_root = root / surface.skill_dir
    if skills_root.is_dir():
        for skill_md in sorted(skills_root.glob("*/SKILL.md")):
            tree[skill_md.relative_to(root).as_posix()] = skill_md.read_text(encoding="utf-8")
    return tree

def write_tree(surface: SurfaceSpec, tree: dict[str, str], dst: Path, stubs: bool = False) -> None:
    dst = Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for rel, content in tree.items():
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
""",
        encoding="utf-8",
    )

    (core / "spec.py").write_text(
        """from dataclasses import dataclass
from pathlib import Path

@dataclass
class ChannelSpec:
    kind: str
    model: str

@dataclass
class BenchPrompts:
    work: str
    domain_word: str
    deliverable_word: str

@dataclass
class BenchSpec:
    name: str
    runs_root: Path
    install_mode: str
    prompts: BenchPrompts
    channels: dict[str, ChannelSpec]
    leak_globs: tuple[str, ...] = ()
""",
        encoding="utf-8",
    )

    (core / "proposer.py").write_text(
        """from dataclasses import dataclass
from pathlib import Path

@dataclass
class Reply:
    label: str
    text: str
    ok: bool = True
    attempts: int = 1
    workspace: Path | None = None
""",
        encoding="utf-8",
    )

    (core / "schedule.py").write_text(
        """import json
import shutil
from pathlib import Path

def mode_token(recursion: str) -> str:
    return {"parallel": "agg", "sequential": "seq"}.get(recursion, recursion)

def run_domain(domain, tasks, k, *, adapter, prop, runs_root, library_root, modes=("agg",)):
    runs_root = Path(runs_root)
    library_root = Path(library_root)
    runs_root.mkdir(parents=True, exist_ok=True)
    library_root.mkdir(parents=True, exist_ok=True)
    lib_entries = {m: 0 for m in modes}
    rows = []
    for idx, task_id in enumerate(tasks):
        before = dict(lib_entries)
        ctx = runs_root / f"ctx_{task_id}"
        hdir = ctx / "harness"
        bdir = ctx / "base_harness"
        cdir = ctx / "candidates"
        shutil.copytree(adapter.base_harness(), hdir, dirs_exist_ok=True)
        shutil.copytree(adapter.base_harness(), bdir, dirs_exist_ok=True)
        cdir.mkdir(parents=True, exist_ok=True)

        base_run = adapter.execute(task_id, bdir, f"{task_id}_base")
        base_score = adapter.score(base_run, task_id)
        (cdir / "cand_base.md").write_text(adapter.deliverable_text(base_run), encoding="utf-8")

        prop.call(adapter.task_prompt(task_id), ctx, f"propose.{task_id}")
        from harness2.core import tree as htree
        cand_tree = htree.read_tree(adapter.substrate.surface(), hdir)
        adapter.validate_tree(cand_tree)

        cand_run = adapter.execute(task_id, hdir, f"{task_id}_cand")
        cand_score = adapter.score(cand_run, task_id)
        (cdir / "cand_improved.md").write_text(adapter.deliverable_text(cand_run), encoding="utf-8")

        prop.call(adapter.task_prompt(task_id), ctx, f"select.{task_id}")
        prop.call(adapter.task_prompt(task_id), ctx, f"experience.{task_id}")
        for m in modes:
            lib_entries[m] += 1
        ok = bool(cand_score and cand_score.get("passed"))
        rows.append({
            "task_id": task_id,
            "status": "ok" if ok else "failed",
            "base_score": base_score,
            "adopted_score": cand_score,
            "library_entries_before": before,
        })
    done = sum(1 for r in rows if r["status"] == "ok")
    failed = len(rows) - done
    return {"domain": domain, "k": k, "modes": list(modes), "done": done, "failed": failed, "tasks": rows}
""",
        encoding="utf-8",
    )
    return root


def ensure_harness2_for_test(tmp_root: Path, cleanup_cb: Callable[[Callable[..., Any]], None]) -> Optional[Path]:
    """Ensure ``harness2`` is importable, staging a temp package only if absent."""
    from dv_agent_platform.harness2_bridge import ensure_harness2_importable

    try:
        ensure_harness2_importable()
        return None
    except ImportError:
        pkg_root = create_minimal_harness2_pkg(tmp_root / "fake_harness2_pkg")
        ensure_harness2_importable(pkg_root)

        def _cleanup() -> None:
            for mod_name in list(sys.modules):
                if mod_name == "harness2" or mod_name.startswith("harness2."):
                    sys.modules.pop(mod_name, None)
            pkg_str = str(pkg_root.resolve())
            while pkg_str in sys.path:
                sys.path.remove(pkg_str)

        cleanup_cb(_cleanup)
        return pkg_root

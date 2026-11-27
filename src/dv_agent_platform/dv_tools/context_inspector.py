"""Read-only DV static context utilities (.f filelist parser & SV/UVM inspector).

Security guarantees:
- All resolved filelist targets, incdirs, libdirs, and inspected SV files must stay within
  ``project_root`` (path traversal outside the workspace raises ``ValueError``).
- Recursive ``-f`` / ``-F`` include cycles raise ``ValueError``.
- Source excerpts returned by ``inspect_sv_context`` are neutralized against tag escape and
  wrapped in ``<untrusted_dv_data>`` fences.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

from ..security.untrusted import detect_injection, wrap_untrusted

_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_MODULE_RE = re.compile(r"(?m)^\s*(?:module|interface)\s+([A-Za-z_][A-Za-z0-9_$]*)\s*(?:#\s*\([^;]*?\))?\s*(?:\(([^;]*?)\))?\s*;", re.S)
_PORT_RE = re.compile(r"\b(input|output|inout)\b[^,;\n)]*?\b([A-Za-z_][A-Za-z0-9_$]*)\s*(?:,|$|\))")
_CLASS_RE = re.compile(r"(?m)^\s*(?:virtual\s+)?class\s+([A-Za-z_][A-Za-z0-9_$]*)(?:\s+extends\s+([A-Za-z_][A-Za-z0-9_$#()]*))?")
_BIND_RE = re.compile(r"(?m)^\s*bind\s+([A-Za-z_][A-Za-z0-9_$.]*)\s+([A-Za-z_][A-Za-z0-9_$]*)(?:\s*#\s*\([^;]*?\))?\s+([A-Za-z_][A-Za-z0-9_$]*)")


def _expand_vars(token: str, env_map: Mapping[str, str]) -> str:
    def _repl(m: re.Match) -> str:
        name = m.group(1) or m.group(2)
        return env_map.get(name, m.group(0))

    return _ENV_VAR_RE.sub(_repl, token)


def _strip_comments(line: str) -> str:
    for marker in ("//", "#"):
        idx = line.find(marker)
        if idx != -1:
            line = line[:idx]
    return line.strip()


def _resolve_contained(raw_path: str, base_dir: Path, project_root: Path) -> Path:
    p = Path(raw_path)
    target = (p if p.is_absolute() else base_dir / p).resolve()
    try:
        target.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(f"Path escapes project_root: {raw_path!r}") from exc
    return target


def parse_filelist(
    filelist_path: Path | str,
    project_root: Path | str,
    env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Parse a VCS/SystemVerilog ``.f`` filelist recursively within ``project_root``."""
    root = Path(project_root).resolve()
    env_map = dict(os.environ if env is None else env)
    env_map.setdefault("DV_PROJECT_ROOT", str(root))
    env_map.setdefault("GEMINI_PROJECT_DIR", str(root))

    start_file = _resolve_contained(str(filelist_path), root, root)
    if not start_file.is_file():
        raise FileNotFoundError(f"Filelist not found: {start_file}")

    files: List[str] = []
    incdirs: List[str] = []
    libdirs: List[str] = []
    libfiles: List[str] = []
    defines: List[str] = []
    seen_files: Set[str] = set()

    def _walk(fpath: Path, stack: Set[Path]) -> None:
        if fpath in stack:
            raise ValueError(f"Recursive filelist include cycle detected: {fpath.relative_to(root).as_posix()}")
        stack.add(fpath)
        try:
            for raw_line in fpath.read_text(encoding="utf-8", errors="replace").splitlines():
                cleaned = _strip_comments(raw_line)
                if not cleaned:
                    continue
                tokens = cleaned.split()
                i = 0
                while i < len(tokens):
                    tok = _expand_vars(tokens[i], env_map)
                    if tok.startswith("+incdir+"):
                        for part in tok[len("+incdir+"):].split("+"):
                            if part:
                                d = _resolve_contained(part, fpath.parent, root)
                                rel = d.relative_to(root).as_posix()
                                if rel not in incdirs:
                                    incdirs.append(rel)
                        i += 1
                    elif tok.startswith("+define+"):
                        defines.append(tok[len("+define+"):])
                        i += 1
                    elif tok in {"-f", "-F"}:
                        if i + 1 >= len(tokens):
                            raise ValueError(f"Missing target after {tok} in {fpath.name}")
                        sub_tok = _expand_vars(tokens[i + 1], env_map)
                        base = fpath.parent if tok == "-F" else root
                        sub_path = _resolve_contained(sub_tok, base, root)
                        if not sub_path.is_file():
                            raise FileNotFoundError(f"Included filelist not found: {sub_tok}")
                        _walk(sub_path, stack)
                        i += 2
                    elif tok == "-y":
                        if i + 1 >= len(tokens):
                            raise ValueError(f"Missing directory after -y in {fpath.name}")
                        d = _resolve_contained(_expand_vars(tokens[i + 1], env_map), fpath.parent, root)
                        rel = d.relative_to(root).as_posix()
                        if rel not in libdirs:
                            libdirs.append(rel)
                        i += 2
                    elif tok == "-v":
                        if i + 1 >= len(tokens):
                            raise ValueError(f"Missing file after -v in {fpath.name}")
                        lf = _resolve_contained(_expand_vars(tokens[i + 1], env_map), fpath.parent, root)
                        rel = lf.relative_to(root).as_posix()
                        if rel not in libfiles:
                            libfiles.append(rel)
                        i += 2
                    elif tok.startswith("+") or tok.startswith("-"):
                        i += 1
                    else:
                        resolved = _resolve_contained(tok, root, root)
                        rel = resolved.relative_to(root).as_posix()
                        if rel not in seen_files:
                            seen_files.add(rel)
                            files.append(rel)
                        i += 1
        finally:
            stack.remove(fpath)

    _walk(start_file, set())
    return {
        "filelist": start_file.relative_to(root).as_posix(),
        "files": files,
        "incdirs": incdirs,
        "libdirs": libdirs,
        "libfiles": libfiles,
        "defines": defines,
    }


def _neutralize_fence(text: str) -> str:
    return re.sub(r"(?i)</\s*untrusted_dv_data\s*>", "[/untrusted_dv_data]", text)


def inspect_sv_context(
    files: Sequence[Path | str],
    project_root: Path | str,
    max_excerpt_chars: int = 1200,
) -> Dict[str, Any]:
    """Extract static SV/UVM structural context with ``<untrusted_dv_data>`` fencing."""
    root = Path(project_root).resolve()
    modules: List[Dict[str, Any]] = []
    classes: List[Dict[str, Any]] = []
    ral_blocks: List[Dict[str, Any]] = []
    binds: List[Dict[str, Any]] = []
    fenced_excerpts: Dict[str, str] = {}
    alerts: List[str] = []

    for f in files:
        resolved = _resolve_contained(str(f), root, root)
        if not resolved.is_file():
            raise FileNotFoundError(f"SV source file not found: {f}")
        rel = resolved.relative_to(root).as_posix()
        raw_text = resolved.read_text(encoding="utf-8", errors="replace")

        for m in _MODULE_RE.finditer(raw_text):
            mod_name = m.group(1)
            port_block = m.group(2) or ""
            ports = [{"direction": pm.group(1), "name": pm.group(2)} for pm in _PORT_RE.finditer(port_block)]
            line_no = raw_text[: m.start()].count("\n") + 1
            modules.append({"name": mod_name, "source": rel, "line": line_no, "ports": ports})

        for m in _CLASS_RE.finditer(raw_text):
            cls_name = m.group(1)
            base_name = m.group(2) or ""
            line_no = raw_text[: m.start()].count("\n") + 1
            entry = {"name": cls_name, "extends": base_name, "source": rel, "line": line_no}
            classes.append(entry)
            if "uvm_reg_block" in base_name or "uvm_reg" in base_name:
                ral_blocks.append(entry)

        for m in _BIND_RE.finditer(raw_text):
            line_no = raw_text[: m.start()].count("\n") + 1
            binds.append({
                "target": m.group(1),
                "checker": m.group(2),
                "instance": m.group(3),
                "source": rel,
                "line": line_no,
            })

        fenced_excerpts[rel] = wrap_untrusted(raw_text, source=rel, max_chars=max_excerpt_chars)
        for hit in detect_injection(raw_text):
            alerts.append(f"[SECURITY_ALERT: {hit.kind}] {rel}:{hit.line}: {_neutralize_fence(hit.excerpt)}")

    return {
        "files_inspected": list(fenced_excerpts.keys()),
        "modules": modules,
        "classes": classes,
        "ral_blocks": ral_blocks,
        "binds": binds,
        "fenced_excerpts": fenced_excerpts,
        "security_alerts": alerts,
    }

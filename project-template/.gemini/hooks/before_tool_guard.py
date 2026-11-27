#!/usr/bin/env python3
"""Gemini CLI BeforeTool Hook — Deterministic DV Safety & Governance Guard.

Reads the hook event JSON from stdin (`tool_name`, `tool_input`, `cwd`) and returns a
JSON HookOutput decision (`allow`, `ask`, or `deny`). Self-contained (stdlib only) so it
works even before `dv_agent_platform` is installed on PYTHONPATH.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

SPLIT_RE = re.compile(r"\|\||&&|;|\||\n|\$\(|`|\)")
P4_OPTS_WITH_VALUE = {"-c", "-u", "-p", "-P", "-H", "-d", "-C", "-L", "-Q", "-x", "-z", "-r", "-v"}
P4_FORBIDDEN = {
    "submit", "obliterate", "push", "fetch", "unsubmit", "resubmit", "revert", "unshelve",
    "populate", "copy", "merge", "integrate", "integ", "passwd", "protect", "admin", "trust",
    "archive", "restore", "retype", "undo",
}
P4_DESTRUCTIVE_FLAG_CMDS = {"change", "client", "user", "branch", "label", "stream", "workspace", "shelve"}

FORBIDDEN_PATTERNS = [
    (re.compile(r"^git\b.*\b(push|reset\s+--hard|clean\s+-[a-z]*f)"), "Destructive/remote git operations are forbidden"),
    (re.compile(r"^(sudo|su|doas)\b"), "Privilege escalation is forbidden"),
    (re.compile(r"^rm\b.*\s-[a-zA-Z]*[rR][a-zA-Z]*f|^rm\b.*\s-[a-zA-Z]*f[a-zA-Z]*[rR]"), "Recursive forced deletion is forbidden"),
    (re.compile(r"^(curl|wget|scp|sftp|ftp|rsync|nc|ncat|netcat|ssh|telnet)\b"), "Network transfer requires organizational authorization"),
    (re.compile(r"^(chmod|chown)\b.*\s-R\b"), "Recursive permission changes are forbidden"),
    (re.compile(r"^(dd|mkfs|shutdown|reboot)\b"), "System-level destructive command"),
]

PROTECTED_REL_PREFIXES = (".gemini/policies", ".gemini/hooks")


WIN_EXE_PREFIX_RE = re.compile(r'^(?P<q>["\']?)(?P<path>[A-Za-z]:\\[^\n"]*?\.(?:exe|cmd|bat))(?P=q)(?=\s|$)', re.I)


def _normalize_exe(tok: str) -> str:
    norm = tok.replace("\\", "/")
    if "/" in norm and not norm.startswith("./"):
        norm = norm.rsplit("/", 1)[-1]
    low = norm.lower()
    for ext in (".exe", ".cmd", ".bat"):
        if low.endswith(ext) and len(norm) > len(ext):
            norm = norm[: -len(ext)]
            break
    if norm.lower() == "p4":
        return "p4"
    return norm.lower() if norm.lower() in {"git", "sudo", "su", "doas", "rm", "curl", "wget", "scp", "ssh"} else norm


def _tokens(segment: str) -> list[str]:
    seg = (segment or "").strip()
    m_win = WIN_EXE_PREFIX_RE.match(seg)
    if m_win:
        exe = _normalize_exe(m_win.group("path"))
        rest = seg[m_win.end():].strip()
        try:
            rest_toks = shlex.split(rest, posix=True) if rest else []
        except ValueError:
            rest_toks = rest.split()
        return [exe] + rest_toks
    try:
        toks = shlex.split(seg, posix=False)
    except ValueError:
        toks = seg.split()
    toks = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in {"'", '"'} else t for t in toks]
    while toks and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]):
        toks.pop(0)
    if toks:
        toks[0] = _normalize_exe(toks[0])
    return toks


def _check_shell(command: str, allow_net: bool) -> tuple[str, str]:
    segments = [s.strip() for s in SPLIT_RE.split(command or "") if s and s.strip()]
    for seg in segments:
        toks = _tokens(seg)
        if not toks:
            continue
        norm = " ".join(toks)
        if toks[0] == "p4":
            i = 1
            sub = None
            rest: list[str] = []
            while i < len(toks):
                if toks[i] in P4_OPTS_WITH_VALUE:
                    i += 2
                    continue
                if toks[i].startswith("-"):
                    i += 1
                    continue
                sub = toks[i]
                rest = toks[i + 1:]
                break
            if sub in P4_FORBIDDEN:
                return "deny", f"Perforce '{sub}' is permanently prohibited (human-only)."
            if sub in P4_DESTRUCTIVE_FLAG_CMDS and any(a in ("-d", "-f", "-D") for a in rest):
                return "deny", f"Destructive 'p4 {sub}' flags are forbidden."
            continue
        for pat, reason in FORBIDDEN_PATTERNS:
            if pat.search(norm):
                if allow_net and reason.startswith("Network transfer"):
                    continue
                return "deny", reason
    return "allow", "Passed deterministic DV command guard"


def _check_write_path(file_path: str, cwd: str) -> tuple[str, str]:
    if not file_path:
        return "allow", "No file_path"
    root = Path(cwd or os.getcwd()).resolve()
    target = (Path(file_path) if Path(file_path).is_absolute() else root / file_path).resolve()
    try:
        rel = target.relative_to(root).as_posix()
    except ValueError:
        return "deny", f"Write outside project workspace is forbidden: {file_path}"
    approval_file = os.environ.get("DV_APPROVAL_FILE")
    if approval_file and target == Path(approval_file).resolve():
        return "deny", "Modifying DV_APPROVAL_FILE is forbidden."
    for pref in PROTECTED_REL_PREFIXES:
        if rel == pref or rel.startswith(pref + "/"):
            return "deny", f"Modifying protected governance path '{rel}' is forbidden."
    return "allow", "Workspace path valid (Policy Engine still enforces ask_user)"


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({"decision": "allow"}))
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"decision": "deny", "reason": "Malformed BeforeTool JSON payload"}))
        return 0

    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "")
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    cwd = str(payload.get("cwd") or os.environ.get("GEMINI_PROJECT_DIR") or os.getcwd())
    allow_net = str(os.environ.get("DV_ALLOW_EXTERNAL_UPLOAD", "false")).lower() in {"1", "true", "yes"}

    if tool_name in {"run_shell_command", "ShellTool"}:
        cmd = str(tool_input.get("command", ""))
        decision, reason = _check_shell(cmd, allow_net)
        if decision == "deny":
            print(json.dumps({"decision": "deny", "reason": reason}))
            return 0

    if tool_name in {"write_file", "replace"}:
        fpath = str(tool_input.get("file_path") or tool_input.get("path") or "")
        decision, reason = _check_write_path(fpath, cwd)
        if decision == "deny":
            print(json.dumps({"decision": "deny", "reason": reason}))
            return 0

    print(json.dumps({"decision": "allow"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

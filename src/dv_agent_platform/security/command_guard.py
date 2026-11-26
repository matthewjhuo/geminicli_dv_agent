"""Shell command classification shared by the Python platform.

The BeforeTool hook (``project-template/.gemini/hooks/before_tool_guard.py``) embeds an
equivalent copy so it can run without this package installed; a parity test keeps the
two in sync.

Categories:
* ``forbidden`` – never run (Perforce submit/obliterate/..., network upload, sudo, rm -rf ...)
* ``approval``  – EDA jobs, source/Perforce preparation, interpreters, unknown commands
* ``readonly``  – inspection-only commands
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import List, Optional, Tuple

_SPLIT_RE = re.compile(r"\|\||&&|;|\||\n|\$\(|`|\)")

# --- Perforce -------------------------------------------------------------------------
P4_OPTS_WITH_VALUE = {"-c", "-u", "-p", "-P", "-H", "-d", "-C", "-L", "-Q", "-x", "-z", "-r", "-v"}
P4_FORBIDDEN = {
    "submit", "obliterate", "push", "fetch", "unsubmit", "resubmit", "revert", "unshelve",
    "populate", "copy", "merge", "integrate", "integ", "passwd", "protect", "admin", "trust",
    "archive", "restore", "retype", "undo",
}
P4_READONLY = {
    "status", "opened", "changes", "describe", "filelog", "annotate", "diff", "diff2", "info",
    "where", "fstat", "print", "have", "files", "dirs", "sizes", "users", "clients", "labels",
    "branches", "interchanges", "cstat", "help", "verify",
}
P4_DESTRUCTIVE_FLAG_CMDS = {"change", "client", "user", "branch", "label", "stream", "workspace", "shelve"}

# --- Generic --------------------------------------------------------------------------
FORBIDDEN_RULES = [
    (re.compile(r"^git\b.*\b(push|reset\s+--hard|clean\s+-[a-z]*f)"), "Destructive/remote git operations are forbidden"),
    (re.compile(r"^(sudo|su|doas)\b"), "Privilege escalation is forbidden"),
    (re.compile(r"^rm\b.*\s-[a-zA-Z]*[rR][a-zA-Z]*f|^rm\b.*\s-[a-zA-Z]*f[a-zA-Z]*[rR]"), "Recursive forced deletion is forbidden"),
    (re.compile(r"^(curl|wget|scp|sftp|ftp|rsync|nc|ncat|netcat|ssh|telnet)\b"), "Network transfer requires organizational authorization"),
    (re.compile(r"^(chmod|chown)\b.*\s-R\b"), "Recursive permission changes are forbidden"),
    (re.compile(r"^(dd|mkfs|shutdown|reboot)\b"), "System-level destructive command"),
]

APPROVAL_PREFIXES = [
    "vcs", "simv", "./simv", "urg", "verdi", "vcf", "xrun", "irun", "dve", "dc_shell", "jg",
    "make", "gmake", "bsub", "qsub", "srun", "nbjob",
    "python", "python3", "perl", "bash", "sh", "zsh", "csh", "tcsh", "tclsh", "node",
    "mv", "cp", "rm", "sed -i", "tee", "touch", "mkdir", "ln", "kill",
]

READONLY_PREFIXES = [
    "grep", "egrep", "rg", "head", "tail", "wc", "ls", "cat", "less", "file", "stat", "du",
    "diff", "sort", "uniq", "cut", "awk", "sed -n", "find", "which", "pwd", "echo", "date",
    "git status", "git log", "git diff", "git show", "git blame",
]

_FIND_WRITE = re.compile(r"\s-(delete|exec|execdir|ok|fprint)\b")
_AWK_WRITE = re.compile(r"system\s*\(|>\s*\"")
_REDIRECT = re.compile(r"(^|[^0-9&>])>{1,2}\s*[^&\s>]")


@dataclass(frozen=True)
class CommandVerdict:
    category: str
    reason: str
    segment: str = ""


def split_segments(command: str) -> List[str]:
    return [s.strip() for s in _SPLIT_RE.split(command or "") if s and s.strip()]


def _tokens(segment: str) -> List[str]:
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        tokens = segment.split()
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        tokens.pop(0)
    if tokens and "/" in tokens[0] and not tokens[0].startswith("./"):
        tokens[0] = tokens[0].rsplit("/", 1)[-1]
    return tokens


def p4_subcommand(tokens: List[str]) -> Tuple[Optional[str], List[str]]:
    """Return (subcommand, remaining args) for a tokenized ``p4`` invocation."""
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok in P4_OPTS_WITH_VALUE:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return tok, tokens[i + 1:]
    return None, []


def _classify_p4(tokens: List[str], seg: str) -> CommandVerdict:
    sub, rest = p4_subcommand(tokens)
    if sub is None:
        return CommandVerdict("approval", "p4 without subcommand", seg)
    if sub in P4_FORBIDDEN:
        return CommandVerdict("forbidden", f"Perforce '{sub}' is never automated (human-only)", seg)
    if sub in P4_DESTRUCTIVE_FLAG_CMDS and any(a in ("-d", "-f", "-D") for a in rest):
        return CommandVerdict("forbidden", f"Destructive 'p4 {sub}' flags are forbidden", seg)
    if sub in P4_READONLY:
        return CommandVerdict("readonly", f"p4 {sub} is read-only", seg)
    return CommandVerdict("approval", f"p4 {sub} modifies workspace state and requires approval", seg)


def _matches_prefix(norm: str, prefixes: List[str]) -> bool:
    return any(norm == p or norm.startswith(p + " ") for p in prefixes)


def classify_command(command: str, allow_network: bool = False) -> CommandVerdict:
    segments = split_segments(command)
    if not segments:
        return CommandVerdict("approval", "Empty or unparsable command")
    worst = CommandVerdict("readonly", "All segments are inspection-only")
    for seg in segments:
        tokens = _tokens(seg)
        if not tokens:
            continue
        norm = " ".join(tokens)
        if tokens[0] == "p4":
            verdict = _classify_p4(tokens, seg)
            if verdict.category == "forbidden":
                return verdict
            if verdict.category == "approval":
                worst = verdict
            continue
        forbidden = None
        for pattern, reason in FORBIDDEN_RULES:
            if pattern.search(norm):
                if allow_network and reason.startswith("Network transfer"):
                    continue
                forbidden = CommandVerdict("forbidden", reason, seg)
                break
        if forbidden:
            return forbidden
        if _matches_prefix(norm, APPROVAL_PREFIXES):
            worst = CommandVerdict("approval", f"'{tokens[0]}' requires explicit approval", seg)
            continue
        if _matches_prefix(norm, READONLY_PREFIXES):
            if tokens[0] == "find" and _FIND_WRITE.search(norm):
                worst = CommandVerdict("approval", "find with side effects", seg)
            elif tokens[0] == "awk" and _AWK_WRITE.search(norm):
                worst = CommandVerdict("approval", "awk with side effects", seg)
            continue
        worst = CommandVerdict("approval", "Unknown command defaults to approval", seg)
    if worst.category == "readonly" and _REDIRECT.search(command or ""):
        return CommandVerdict("approval", "Output redirection writes files", command)
    return worst

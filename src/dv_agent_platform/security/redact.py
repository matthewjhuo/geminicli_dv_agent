"""Secret redaction for logs, state files and memory entries."""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    re.compile(r"\bsk-[0-9A-Za-z]{20,}\b"),
    re.compile(r"\bgh[pousr]_[0-9A-Za-z]{30,}\b"),
    re.compile(r"\bxox[abpr]-[0-9A-Za-z-]{10,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]
_KV = re.compile(
    r"(?i)\b((?:[a-z0-9]+[_-])?(?:password|passwd|secret|api[_-]?key|token|p4passwd|credential))(\s*[:=]\s*)(['\"]?)([^\s'\"]{6,})(\3)"
)
_SENSITIVE_KEY = re.compile(r"(?i)(password|passwd|secret|api[_-]?key|^token$|auth|credential|p4passwd)")


def redact(text: str) -> str:
    if not text:
        return text
    out = text
    for pat in _PATTERNS:
        out = pat.sub(REDACTED, out)
    out = _KV.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}{m.group(5)}", out)
    return out


def redact_obj(obj: Any, _depth: int = 0) -> Any:
    if _depth > 20:
        return obj
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SENSITIVE_KEY.search(k) and isinstance(v, str) and v:
                result[k] = REDACTED
            else:
                result[k] = redact_obj(v, _depth + 1)
        return result
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v, _depth + 1) for v in obj]
    return obj

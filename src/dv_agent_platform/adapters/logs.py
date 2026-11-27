"""Shared simulation failure recognition; severity summaries are not messages."""
import re

_SUMMARY = re.compile(r"^\s*(?:#\s*)?UVM_(?:ERROR|FATAL)\s*:\s*(\d+)\s*$")
_ERROR = re.compile(r"UVM_FATAL|UVM_ERROR|Error-\[[^\]]+\]|Assertion .* failed|Offending .*|PH_TIMEOUT")


def is_failure_line(line: str) -> bool:
    summary = _SUMMARY.match(line)
    if summary:
        return int(summary.group(1)) > 0
    return bool(_ERROR.search(line))

"""Deterministic Markdown report rendering for persisted Orchestrator run states."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Union

from ..security.redact import redact, redact_obj


def render_run_report(state_or_path: Union[Dict[str, Any], Path, str]) -> str:
    """Render a deterministic, secret-redacted Markdown report from a run state dict or JSON path."""
    if isinstance(state_or_path, (str, Path)):
        path = Path(state_or_path)
        if not path.is_file():
            raise FileNotFoundError(f"Run state file not found: {path}")
        state = json.loads(path.read_text(encoding="utf-8"))
    elif isinstance(state_or_path, dict):
        state = state_or_path
    else:
        raise TypeError(f"Expected dict or Path, got {type(state_or_path).__name__}")

    clean = redact_obj(state)
    run_id = clean.get("run_id", "unknown")
    workflow = clean.get("workflow", "unknown")
    status = clean.get("status", "unknown")
    dry_run = bool(clean.get("dry_run", False))
    agg = clean.get("aggregate") or {}
    decision = agg.get("decision")
    needs_human = bool(agg.get("needs_human", False))
    usage = clean.get("usage") or {}

    lines = [
        f"# DV Orchestrator Run Report (`{run_id}`)",
        "",
        "## Summary",
        f"- **Run ID**: `{run_id}`",
        f"- **Workflow**: `{workflow}`",
        f"- **Status**: `{status}`",
        f"- **Dry Run**: `{dry_run}`",
        f"- **Aggregate Decision**: `{decision}`",
        f"- **Needs Human Review**: `{needs_human}`",
        f"- **Total Tokens**: `{usage.get('total_tokens', 0)}`",
        f"- **Estimated Cost (USD)**: `{usage.get('estimated_cost_usd', 0.0)}`",
        "",
        "## Tasks",
    ]

    tasks = clean.get("tasks") or {}
    for tid in sorted(tasks.keys()):
        t = tasks[tid] or {}
        graph_task = next((item for item in clean.get("graph", {}).get("tasks", []) if item.get("id") == tid), {})
        agent = t.get("agent") or graph_task.get("agent") or t.get("kind") or graph_task.get("kind") or "task"
        t_status = t.get("status", "unknown")
        summary = ""
        out = t.get("output")
        if isinstance(out, dict):
            summary = str(out.get("summary") or "")
        lines.append(f"- **`{tid}`** (`{agent}`) — `{t_status}`" + (f": {summary}" if summary else ""))

    findings = agg.get("findings") or []
    if findings:
        lines.extend(["", "## Aggregated Findings"])
        for f in findings:
            fid = f.get("id", "F")
            ep = f.get("epistemic_effective", f.get("epistemic", "UNKNOWN"))
            claim = f.get("claim", "")
            lines.append(f"- `{fid}` [{ep}]: {claim}")

    alerts = clean.get("security_alerts") or []
    if alerts:
        lines.extend(["", "## Security Alerts"])
        for al in alerts:
            lines.append(f"- {al}")

    return redact("\n".join(lines).rstrip() + "\n")

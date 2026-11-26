"""Command-line entrypoint for the optional Python DV Orchestrator & Platform."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from .config import FrameworkConfig
from .orchestrator.backends import GeminiCliBackend
from .orchestrator.runner import Orchestrator
from .orchestrator.state import StateStore
from .orchestrator.workflows import BUILTIN_WORKFLOWS


def _parse_kv(items: List[str]) -> dict:
    out = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"Expected KEY=VALUE parameter, got {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="dv-agent-platform", description="Agentic DV Platform CLI")
    parser.add_argument("--project-root", default=None, help="Project root directory (defaults to $DV_PROJECT_ROOT or CWD)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list_wf = sub.add_parser("workflows", help="List available DV teamwork workflows")
    p_list_wf.set_defaults(func="workflows")

    p_run = sub.add_parser("run", help="Execute or dry-run a DV multi-agent workflow")
    p_run.add_argument("workflow", help=f"Workflow name ({', '.join(sorted(BUILTIN_WORKFLOWS))})")
    p_run.add_argument("-P", "--param", action="append", default=[], help="Workflow parameter KEY=VALUE")
    p_run.add_argument("--execute", action="store_true", help="Disable dry-run and invoke Gemini CLI in read-only plan mode")

    p_runs = sub.add_parser("runs", help="List persisted workflow runs")
    p_runs.set_defaults(func="runs")

    p_status = sub.add_parser("status", help="Show persisted state of a workflow run")
    p_status.add_argument("run_id")

    args = parser.parse_args(argv)
    root = Path(args.project_root).resolve() if args.project_root else None

    if args.cmd == "workflows":
        print(json.dumps({k: v["description"] for k, v in BUILTIN_WORKFLOWS.items()}, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "runs":
        cfg = FrameworkConfig.load(project_root=root)
        print(json.dumps(StateStore(cfg.runs_dir).list_runs(), indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "status":
        cfg = FrameworkConfig.load(project_root=root)
        print(json.dumps(StateStore(cfg.runs_dir).load(args.run_id), indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "run":
        params = _parse_kv(args.param)
        cfg = FrameworkConfig.load(project_root=root, overrides={"dry_run": not args.execute})
        backend = GeminiCliBackend(cwd=str(cfg.project_root), approval_mode="plan") if args.execute else None
        orch = Orchestrator(cfg, backend=backend)
        state = orch.run_workflow(args.workflow, params=params)
        print(json.dumps({
            "run_id": state["run_id"],
            "status": state["status"],
            "dry_run": state["dry_run"],
            "levels": state["graph"]["levels"],
            "aggregate_decision": (state.get("aggregate") or {}).get("decision"),
            "usage": state.get("usage"),
        }, indent=2, ensure_ascii=False))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())

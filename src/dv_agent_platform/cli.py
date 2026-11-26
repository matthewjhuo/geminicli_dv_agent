"""Command-line entrypoint for the optional Python DV Orchestrator & Platform."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from .config import FrameworkConfig
from .evaluation.harness import EvaluationHarness
from .memory.store import MEMORY_KINDS, MemoryStore
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

    p_mem = sub.add_parser("memory", help="Search project-scoped verified DV memory entries")
    p_mem.add_argument("--query", default="", help="Keyword query across titles, content, and tags")
    p_mem.add_argument(
        "--kind",
        action="append",
        choices=sorted(MEMORY_KINDS),
        default=None,
        help="Optional memory tier filter (repeatable)",
    )
    p_mem.add_argument(
        "--include-unconfirmed",
        action="store_true",
        help="Include candidate/verified entries in addition to human-confirmed entries",
    )
    p_mem.add_argument("--limit", type=int, default=5, help="Maximum entries to return")

    p_eval = sub.add_parser("evaluate", help="Inspect or run the DV golden benchmark dataset")
    p_eval.add_argument(
        "--dataset",
        default="evaluation/golden/dv_golden_cases.json",
        help="Path to golden benchmark JSON (relative to project root or absolute)",
    )
    p_eval.add_argument(
        "--case",
        action="append",
        default=None,
        help="Optional case_id filter (repeatable)",
    )
    p_eval.add_argument(
        "--execute",
        action="store_true",
        help="Execute cases against Gemini CLI in read-only plan mode (defaults to dry-run dataset preview)",
    )

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

    if args.cmd == "memory":
        cfg = FrameworkConfig.load(project_root=root)
        if not cfg.memory_db.is_file():
            print(json.dumps({
                "project_id": cfg.project_id,
                "query": args.query,
                "only_confirmed": not args.include_unconfirmed,
                "count": 0,
                "entries": [],
                "note": "No project memory database initialized yet.",
            }, indent=2, ensure_ascii=False))
            return 0
        store = MemoryStore(cfg.memory_db, cfg.project_root)
        entries = store.retrieve(
            project_id=cfg.project_id,
            query=args.query,
            kinds=args.kind,
            only_confirmed=not args.include_unconfirmed,
            limit=args.limit,
        )
        for item in entries:
            item["stale"] = store.check_staleness(item["entry_id"], project_id=cfg.project_id)
        print(json.dumps({
            "project_id": cfg.project_id,
            "query": args.query,
            "only_confirmed": not args.include_unconfirmed,
            "count": len(entries),
            "entries": entries,
        }, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "evaluate":
        cfg = FrameworkConfig.load(project_root=root)
        ds_path = Path(args.dataset)
        if not ds_path.is_absolute():
            ds_path = cfg.project_root / ds_path
        cases = EvaluationHarness.load_golden_dataset(ds_path)
        if args.case:
            allowed = set(args.case)
            cases = [c for c in cases if c.case_id in allowed]
        if not args.execute:
            print(json.dumps({
                "mode": "dry_run_preview",
                "dataset": str(ds_path),
                "total_cases": len(cases),
                "cases": [
                    {
                        "case_id": c.case_id,
                        "workflow": c.workflow,
                        "expected_decision": c.expected_decision,
                        "expected_sources": c.expected_sources,
                        "expect_security_alert": c.expect_security_alert,
                        "description": c.description,
                    }
                    for c in cases
                ],
            }, indent=2, ensure_ascii=False))
            return 0
        harness = EvaluationHarness(
            cfg,
            backend_factory=lambda _case: GeminiCliBackend(cwd=str(cfg.project_root), approval_mode="plan"),
        )
        report = harness.evaluate(cases)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["passed_cases"] == report["total_cases"] else 1

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

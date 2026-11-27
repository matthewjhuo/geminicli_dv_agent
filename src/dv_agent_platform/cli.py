"""Command-line entrypoint for the optional Python DV Orchestrator & Platform."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from .config import FrameworkConfig
from .errors import DVAgentError
from .evaluation.harness import EvaluationHarness
from .evaluation.mutation_oracle import run_mutation_oracle_file
from .memory.store import MEMORY_KINDS, MemoryStore
from .orchestrator.backends import GeminiCliBackend
from .orchestrator.reports import render_run_report
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


def _orchestrator(cfg, backend):
    memory_provider = MemoryStore(cfg.memory_db, cfg.project_root).format_context if cfg.memory_db.is_file() else None
    return Orchestrator(cfg, backend=backend, memory_context_provider=memory_provider)


def _print_run(state) -> int:
    print(json.dumps({
        "run_id": state["run_id"], "status": state["status"], "dry_run": state["dry_run"],
        "levels": state["graph"]["levels"],
        "aggregate_decision": (state.get("aggregate") or {}).get("decision"), "usage": state.get("usage"),
    }, indent=2, ensure_ascii=False))
    return 0 if state["status"] in {"succeeded", "planned"} else 1


def _handle_memory(root: Optional[Path], mem_args: List[str]) -> int:
    sub_actions = {"add-candidate", "verify", "promote", "check-staleness", "search"}
    action = mem_args[0] if mem_args and mem_args[0] in sub_actions else "search"
    rest = mem_args[1:] if mem_args and mem_args[0] in sub_actions else mem_args

    cfg = FrameworkConfig.load(project_root=root)

    if action == "search":
        p = argparse.ArgumentParser(prog="dv-agent-platform memory")
        p.add_argument("--query", default="", help="Keyword query across titles, content, and tags")
        p.add_argument("--kind", action="append", choices=sorted(MEMORY_KINDS), default=None)
        p.add_argument("--include-unconfirmed", action="store_true")
        p.add_argument("--limit", type=int, default=5)
        ns = p.parse_args(rest)
        if not cfg.memory_db.is_file():
            print(json.dumps({
                "project_id": cfg.project_id,
                "query": ns.query,
                "only_confirmed": not ns.include_unconfirmed,
                "count": 0,
                "entries": [],
                "note": "No project memory database initialized yet.",
            }, indent=2, ensure_ascii=False))
            return 0
        store = MemoryStore(cfg.memory_db, cfg.project_root)
        entries = store.retrieve(
            project_id=cfg.project_id,
            query=ns.query,
            kinds=ns.kind,
            only_confirmed=not ns.include_unconfirmed,
            limit=ns.limit,
        )
        for item in entries:
            item["stale"] = store.check_staleness(item["entry_id"], project_id=cfg.project_id)
        print(json.dumps({
            "project_id": cfg.project_id,
            "query": ns.query,
            "only_confirmed": not ns.include_unconfirmed,
            "count": len(entries),
            "entries": entries,
        }, indent=2, ensure_ascii=False))
        return 0

    store = MemoryStore(cfg.memory_db, cfg.project_root)

    if action == "add-candidate":
        p = argparse.ArgumentParser(prog="dv-agent-platform memory add-candidate")
        p.add_argument("--kind", required=True, choices=sorted(MEMORY_KINDS))
        p.add_argument("--title", required=True)
        p.add_argument("--content", required=True)
        p.add_argument("--source", required=True)
        p.add_argument("--revision", required=True)
        p.add_argument("--line-start", type=int, default=None)
        p.add_argument("--line-end", type=int, default=None)
        p.add_argument("--excerpt", default=None)
        p.add_argument("--tag", action="append", default=[])
        ns = p.parse_args(rest)
        prov: dict = {"source": ns.source, "revision": ns.revision}
        if ns.line_start is not None:
            prov["line_start"] = ns.line_start
        if ns.line_end is not None:
            prov["line_end"] = ns.line_end
        if ns.excerpt is not None:
            prov["excerpt"] = ns.excerpt
        entry_id = store.add_candidate(
            project_id=cfg.project_id,
            kind=ns.kind,
            title=ns.title,
            content=ns.content,
            provenance=[prov],
            tags=ns.tag,
        )
        entry = store.get(entry_id, project_id=cfg.project_id)
        print(json.dumps(entry, indent=2, ensure_ascii=False))
        return 0

    if action == "verify":
        p = argparse.ArgumentParser(prog="dv-agent-platform memory verify")
        p.add_argument("entry_id")
        ns = p.parse_args(rest)
        entry = store.verify_entry(ns.entry_id, project_id=cfg.project_id)
        print(json.dumps(entry, indent=2, ensure_ascii=False))
        return 0

    if action == "promote":
        p = argparse.ArgumentParser(prog="dv-agent-platform memory promote")
        p.add_argument("entry_id")
        p.add_argument("--approver", required=True)
        p.add_argument("--approval-id", required=True)
        ns = p.parse_args(rest)
        entry = store.promote_to_confirmed(
            ns.entry_id,
            project_id=cfg.project_id,
            approver=ns.approver,
            approval_id=ns.approval_id,
        )
        print(json.dumps(entry, indent=2, ensure_ascii=False))
        return 0

    if action == "check-staleness":
        p = argparse.ArgumentParser(prog="dv-agent-platform memory check-staleness")
        p.add_argument("entry_id")
        ns = p.parse_args(rest)
        stale = store.check_staleness(ns.entry_id, project_id=cfg.project_id)
        entry = store.get(ns.entry_id, project_id=cfg.project_id)
        print(json.dumps({
            "entry_id": ns.entry_id,
            "project_id": cfg.project_id,
            "stale": stale,
            "status": entry["status"],
        }, indent=2, ensure_ascii=False))
        return 0

    return 1


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

    p_resume = sub.add_parser("resume", help="Preview or explicitly resume an interrupted workflow, preserving charged usage")
    p_resume.add_argument("run_id")
    p_resume.add_argument("--execute", action="store_true", help="Resume with Gemini CLI in read-only plan mode")

    p_runs = sub.add_parser("runs", help="List persisted workflow runs")
    p_runs.set_defaults(func="runs")

    p_status = sub.add_parser("status", help="Show persisted state of a workflow run")
    p_status.add_argument("run_id")

    p_report = sub.add_parser("report", help="Render a deterministic Markdown report for a persisted run")
    p_report.add_argument("run_id")

    p_mem = sub.add_parser("memory", add_help=False, help="Search or manage project-scoped DV memory entries")

    p_eval = sub.add_parser("evaluate", help="Inspect or run the DV golden benchmark dataset")
    p_eval.add_argument(
        "--dataset",
        default="evaluation/golden/dv_golden_cases.json",
        help="Path to golden benchmark JSON (relative to project root or absolute)",
    )
    p_eval.add_argument("--case", action="append", default=None, help="Optional case_id filter (repeatable)")
    p_eval.add_argument(
        "--execute",
        action="store_true",
        help="Execute cases against Gemini CLI in read-only plan mode (defaults to dry-run dataset preview)",
    )
    p_eval.add_argument(
        "--mutation-ladder",
        action="store_true",
        help="Include Survey 5-stage RTL Mutation / Fault-Injection Oracle Evaluation Ladder",
    )
    p_eval.add_argument(
        "--sva-file",
        default="fixtures/verif/axi_fifo_sva.sv",
        help="Project-relative SVA file evaluated by --mutation-ladder",
    )

    args, extra = parser.parse_known_args(argv)
    if args.cmd != "memory" and extra:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    root = Path(args.project_root).resolve() if args.project_root else None

    try:
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

        if args.cmd == "resume":
            cfg = FrameworkConfig.load(project_root=root)
            if not args.execute:
                state = StateStore(cfg.runs_dir).load(args.run_id)
                print(json.dumps({"mode": "resume_preview", "run_id": args.run_id,
                                  "status": state["status"], "usage": state.get("usage")}, indent=2))
                return 0
            backend = GeminiCliBackend(cwd=str(cfg.project_root), approval_mode="plan")
            return _print_run(_orchestrator(cfg, backend).resume(args.run_id))

        if args.cmd == "report":
            cfg = FrameworkConfig.load(project_root=root)
            state_path = cfg.runs_dir / args.run_id / "state.json"
            print(render_run_report(state_path))
            return 0

        if args.cmd == "memory":
            return _handle_memory(root, extra)

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
                payload = {
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
                }
                if args.mutation_ladder:
                    sva_path = (cfg.project_root / args.sva_file).resolve()
                    sva_path.relative_to(cfg.project_root)
                    payload["evaluation_ladder"] = run_mutation_oracle_file(
                        sva_path, include_scoreboard_integrity=True
                    )
                print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
            harness = EvaluationHarness(
                cfg,
                backend_factory=lambda _case: GeminiCliBackend(cwd=str(cfg.project_root), approval_mode="plan"),
            )
            if args.mutation_ladder:
                report = harness.evaluate_with_ladder(cases, sva_path=args.sva_file)
                print(json.dumps(report, indent=2, ensure_ascii=False))
                return 0 if report["signoff_ready"] else 1
            report = harness.evaluate(cases)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0 if report["passed_cases"] == report["total_cases"] else 1

        if args.cmd == "run":
            params = _parse_kv(args.param)
            cfg = FrameworkConfig.load(project_root=root, overrides={"dry_run": not args.execute})
            backend = GeminiCliBackend(cwd=str(cfg.project_root), approval_mode="plan") if args.execute else None
            orch = _orchestrator(cfg, backend)
            state = orch.run_workflow(args.workflow, params=params)
            return _print_run(state)
    except (DVAgentError, ValueError, KeyError, FileNotFoundError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    sys.exit(main())

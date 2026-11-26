#!/usr/bin/env python3
"""Run Google Research Harness^2 recursive optimization over the Agentic DV Framework."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dv_agent_platform.harness2_bridge import (  # noqa: E402
    DVHarness2Adapter,
    DVOfflineProposer,
    ensure_harness2_importable,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("dry_sim", "gemini_cli"),
        default="dry_sim",
        help="dry_sim runs offline deterministic verification; gemini_cli invokes real Gemini CLI in read-only plan mode.",
    )
    parser.add_argument(
        "--recursion",
        choices=("parallel", "sequential", "both"),
        default="parallel",
        help="Harness^2 task-level recursion mode.",
    )
    parser.add_argument("--k", type=int, default=1, help="Task-level recursion budget k (>=1).")
    parser.add_argument(
        "--tasks",
        nargs="*",
        default=None,
        help="Optional subset of golden DV case IDs (e.g. GOLD-TRIAGE-001 GOLD-SEC-004).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "runs" / "harness2_dv",
        help="Output directory for Harness^2 runs and improvement library.",
    )
    parser.add_argument(
        "--harness2-dir",
        type=Path,
        default=None,
        help="Optional path to google-research/harness2 checkout (defaults to active venv or $HARNESS2_REPO_DIR).",
    )
    args = parser.parse_args(argv)

    ensure_harness2_importable(args.harness2_dir)
    from harness2.core.schedule import mode_token, run_domain

    adapter = DVHarness2Adapter(
        framework_root=REPO_ROOT,
        output_root=args.output,
        execution_mode=args.mode,
        task_filter=args.tasks,
        harness2_dir=args.harness2_dir,
    )
    proposer = DVOfflineProposer(adapter.spec.channels["proposer"])
    modes = ("agg", "seq") if args.recursion == "both" else (mode_token(args.recursion),)

    summary = run_domain(
        "soc_dv",
        adapter.tasks(),
        args.k,
        adapter=adapter,
        prop=proposer,
        runs_root=adapter.spec.runs_root,
        library_root=args.output / "library" / f"k{args.k}",
        modes=modes,
    )
    summary["execution_count"] = adapter.execution_count
    summary["editor_calls"] = len(proposer._calls)

    report_path = args.output / "dv_harness2_summary.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Harness^2 DV run finished: {summary['done']} done, {summary['failed']} failed -> {report_path}")
    return int(summary["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())

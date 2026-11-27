"""Golden test-case evaluation harness for Verification-Grounded AI.

Measures four primary engineering metrics across a golden dataset:
1. ``accuracy``             – exact match on expected root-cause classification / decision
2. ``evidence_correctness`` – harmonic/mean score of (a) verified local provenance ratio
                              and (b) recall of mandatory golden evidence sources/lines
3. ``cost_usd``             – API token consumption & USD cost via UsageTracker
4. ``execution_time_s``     – wall-clock runtime per case and in aggregate
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..config import FrameworkConfig
from ..orchestrator.backends import AgentBackend
from ..orchestrator.runner import Orchestrator
from .mutation_oracle import run_mutation_oracle_file


@dataclass
class GoldenCase:
    case_id: str
    workflow: str
    params: Dict[str, Any]
    expected_decision: Optional[str]
    expected_sources: List[str]
    expect_security_alert: bool = False
    description: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GoldenCase":
        return cls(
            case_id=d["case_id"],
            workflow=d["workflow"],
            params=dict(d.get("params") or {}),
            expected_decision=d.get("expected_decision"),
            expected_sources=list(d.get("expected_sources") or []),
            expect_security_alert=bool(d.get("expect_security_alert", False)),
            description=d.get("description", ""),
        )


class EvaluationHarness:
    def __init__(
        self,
        config: FrameworkConfig,
        backend_factory: Callable[[GoldenCase], AgentBackend],
    ):
        self.config = config
        self.backend_factory = backend_factory

    @staticmethod
    def load_golden_dataset(path: Path) -> List[GoldenCase]:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        cases = raw["cases"] if isinstance(raw, dict) else raw
        return [GoldenCase.from_dict(c) for c in cases]

    def evaluate(self, cases: List[GoldenCase]) -> Dict[str, Any]:
        case_reports: List[Dict[str, Any]] = []
        t0_all = time.perf_counter()

        for case in cases:
            run_cfg = FrameworkConfig.load(
                project_root=self.config.project_root,
                overrides={
                    "project_id": self.config.project_id,
                    "state_dir": str(self.config.state_dir),
                    "dry_run": False,
                    "pricing": self.config.pricing,
                    "token_budget": self.config.token_budget,
                    "max_concurrency": self.config.max_concurrency,
                },
            )
            backend = self.backend_factory(case)
            orch = Orchestrator(run_cfg, backend=backend)

            t0 = time.perf_counter()
            state = orch.run_workflow(case.workflow, params=case.params)
            elapsed = time.perf_counter() - t0

            agg = state.get("aggregate") or {}
            actual_decision = agg.get("decision")
            decision_correct = actual_decision == case.expected_decision

            # Compute evidence correctness:
            # (1) fraction of reported evidence items verified locally
            findings = agg.get("findings") or []
            total_ev = 0
            verified_ev = 0
            cited_sources = set()
            for f in findings:
                for ev_entry in f.get("evidence") or []:
                    total_ev += 1
                    if ev_entry.get("status") == "verified":
                        verified_ev += 1
                        cited_sources.add(ev_entry["evidence"].get("source"))
            verified_ratio = (verified_ev / total_ev) if total_ev > 0 else 0.0

            # (2) recall against golden expected_sources
            if case.expected_sources:
                hit_sources = sum(1 for s in case.expected_sources if s in cited_sources)
                recall_ratio = hit_sources / len(case.expected_sources)
            else:
                recall_ratio = 1.0
            ev_score = round(0.5 * verified_ratio + 0.5 * recall_ratio, 4)

            alerts_ok = (len(state.get("security_alerts") or []) > 0) if case.expect_security_alert else True
            passed = bool(decision_correct and ev_score >= 0.85 and alerts_ok and state["status"] == "succeeded")

            usage = state.get("usage") or {}
            case_reports.append({
                "case_id": case.case_id,
                "workflow": case.workflow,
                "passed": passed,
                "decision_expected": case.expected_decision,
                "decision_actual": actual_decision,
                "accuracy": 1.0 if decision_correct else 0.0,
                "evidence_correctness": ev_score,
                "verified_evidence_ratio": round(verified_ratio, 4),
                "golden_source_recall": round(recall_ratio, 4),
                "security_alert_check": alerts_ok,
                "tokens": usage.get("total_tokens", 0),
                "cost_usd": usage.get("estimated_cost_usd", 0.0),
                "execution_time_s": round(elapsed, 4),
            })

        total_time = time.perf_counter() - t0_all
        n = max(1, len(case_reports))
        return {
            "total_cases": len(case_reports),
            "passed_cases": sum(1 for r in case_reports if r["passed"]),
            "accuracy": round(sum(r["accuracy"] for r in case_reports) / n, 4),
            "evidence_correctness": round(sum(r["evidence_correctness"] for r in case_reports) / n, 4),
            "total_tokens": sum(r["tokens"] for r in case_reports),
            "total_cost_usd": round(sum(r["cost_usd"] for r in case_reports), 6),
            "total_execution_time_s": round(total_time, 4),
            "avg_execution_time_s": round(sum(r["execution_time_s"] for r in case_reports) / n, 4),
            "cases": case_reports,
        }

    def evaluate_with_ladder(
        self,
        cases: List[GoldenCase],
        *,
        sva_path: str = "fixtures/verif/axi_fifo_sva.sv",
        include_scoreboard_integrity: bool = True,
        min_kill_rate: float = 0.6,
    ) -> Dict[str, Any]:
        """Run standard golden case evaluation + Survey 5-stage Mutation Oracle Evaluation Ladder."""
        base_report = self.evaluate(cases)
        resolved_sva = (self.config.project_root / sva_path).resolve()
        resolved_sva.relative_to(self.config.project_root)
        ladder = run_mutation_oracle_file(
            resolved_sva,
            include_scoreboard_integrity=include_scoreboard_integrity,
            min_kill_rate=min_kill_rate,
        )
        base_report["evaluation_ladder"] = ladder
        base_report["mutation_kill_rate"] = ladder["mutation_kill_rate"]
        base_report["signoff_ready"] = bool(
            base_report["passed_cases"] == base_report["total_cases"] and ladder["signoff_ready"]
        )
        return base_report

"""Regression & coverage summary adapter."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .base import BaseAdapter

_HEX_RE = re.compile(r"0x[0-9a-fA-F]+")
_NUM_RE = re.compile(r"\b\d+\s*(?:ns|ps|us|fs)\b|\bseed[=\s]+\d+\b", re.I)


def normalize_signature(msg: str) -> str:
    out = _HEX_RE.sub("<HEX>", msg or "")
    out = _NUM_RE.sub("<TIME_OR_SEED>", out)
    return re.sub(r"\s+", " ", out).strip()


class RegressionAdapter(BaseAdapter):
    name = "regression"

    def cluster_failures(self, run_id: str = "latest") -> Dict[str, Any]:
        _ = self.is_mock
        runs = self._load_mock_infra().get("regression", {}).get("failures", [])
        buckets: Dict[str, Dict[str, Any]] = {}
        for item in runs:
            sig = normalize_signature(item.get("raw_error", ""))
            b = buckets.setdefault(sig, {"signature": sig, "count": 0, "seeds": [], "tests": []})
            b["count"] += 1
            b["seeds"].append(item.get("seed"))
            if item.get("test") not in b["tests"]:
                b["tests"].append(item.get("test"))
        ranked = sorted(buckets.values(), key=lambda x: -x["count"])
        self.logger.log("regression_cluster", run_id=run_id, buckets=len(ranked))
        return {"run_id": run_id, "total_failures": len(runs), "buckets": ranked}

    def coverage_summary(self, report_path: str = "fixtures/coverage/urg_dashboard.txt") -> Dict[str, Any]:
        _ = self.is_mock
        path = self._resolve_project_file(report_path)
        text = path.read_text(encoding="utf-8", errors="replace")
        metrics: Dict[str, float] = {}
        for key in ("Total Score", "Line", "Cond", "Toggle", "FSM", "Functional Groups"):
            m = re.search(rf"{re.escape(key)}:\s*([0-9.]+)%", text)
            if m:
                metrics[key.lower().replace(" ", "_")] = float(m.group(1))
        self.logger.log("regression_coverage_summary", report_path=report_path)
        return {"report_path": report_path, "metrics": metrics, "raw_excerpt": text[:2000]}

    def launch(self, suite: str, seeds: int = 10, dry_run: Optional[bool] = None,
               approval_id: Optional[str] = None) -> Dict[str, Any]:
        is_dry = self.config.dry_run if dry_run is None else bool(dry_run)
        details = {"suite": suite, "seeds": int(seeds)}
        if is_dry:
            return {"status": "dry_run", "requires_approval": True, **details}
        decision = self._require_approval("simulation", f"Launch regression suite={suite} seeds={seeds}", details, approval_id)
        mock = self.is_mock
        self.logger.log("regression_launched", mock=mock, approver=decision.approver, **details)
        return {"status": "queued_mock" if mock else "queued", "approved_by": decision.approver, **details}

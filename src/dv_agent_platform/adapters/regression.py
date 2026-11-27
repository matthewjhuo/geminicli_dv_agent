"""Regression & coverage summary adapter."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .base import BaseAdapter

_HEX_RE = re.compile(r"0x[0-9a-fA-F]+")
_NUM_RE = re.compile(r"\b\d+\s*(?:ns|ps|us|fs)\b|\bseed[=\s]+\d+\b", re.I)
_LOG_ERR_RE = re.compile(r"(UVM_FATAL|UVM_ERROR|Error-\[[^\]]+\]|Assertion .* failed|Offending .*|PH_TIMEOUT)")
_TEST_RE = re.compile(r"\+UVM_TESTNAME=([^\s]+)")
_SEED_RE = re.compile(r"\+ntb_random_seed=(\d+)")


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

    def scan_sim_out(self, sim_out_dir: str = "sim_out", pattern: str = "simv*.log") -> Dict[str, Any]:
        _ = self.is_mock
        target_dir = self._resolve_project_file(sim_out_dir)
        if not target_dir.is_dir():
            raise FileNotFoundError(f"Simulation output directory not found: {sim_out_dir}")
        buckets: Dict[str, Dict[str, Any]] = {}
        scanned = 0
        total_failures = 0
        for log_file in sorted(target_dir.rglob(pattern)):
            if not log_file.is_file():
                continue
            scanned += 1
            text = log_file.read_text(encoding="utf-8", errors="replace")
            m_test = _TEST_RE.search(text)
            m_seed = _SEED_RE.search(text)
            test_name = m_test.group(1) if m_test else log_file.stem
            seed_val = m_seed.group(1) if m_seed else "unknown"
            first_err = None
            for line in text.splitlines():
                if _LOG_ERR_RE.search(line):
                    first_err = line.strip()
                    break
            if first_err is None:
                continue
            total_failures += 1
            sig = normalize_signature(first_err)
            b = buckets.setdefault(sig, {"signature": sig, "count": 0, "seeds": [], "tests": [], "logs": []})
            b["count"] += 1
            b["seeds"].append(seed_val)
            if test_name not in b["tests"]:
                b["tests"].append(test_name)
            b["logs"].append(log_file.relative_to(self.config.project_root).as_posix())
        ranked = sorted(buckets.values(), key=lambda x: -x["count"])
        self.logger.log("regression_scan_sim_out", sim_out_dir=sim_out_dir, scanned=scanned, total_failures=total_failures)
        return {
            "sim_out_dir": target_dir.relative_to(self.config.project_root).as_posix(),
            "pattern": pattern,
            "scanned_logs": scanned,
            "total_failures": total_failures,
            "buckets": ranked,
        }

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

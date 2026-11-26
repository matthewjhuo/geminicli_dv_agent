"""Synopsys VCS adapter (read-only log parsing + approval-gated simulation)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .base import BaseAdapter

_ERR_RE = re.compile(r"(UVM_FATAL|UVM_ERROR|Error-\[[^\]]+\]|Assertion .* failed|Offending .*|PH_TIMEOUT)")
_TIME_RE = re.compile(r"(?:@|\bat\s+)(\d+\s*(?:fs|ps|ns|us|ms|s))", re.I)


class VCSAdapter(BaseAdapter):
    name = "vcs"

    def parse_log(self, log_path: str, context_lines: int = 5, max_hits: int = 20) -> Dict[str, Any]:
        _ = self.is_mock  # validates mode policy
        path = self._resolve_project_file(log_path)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        hits: List[Dict[str, Any]] = []
        seed: Optional[str] = None
        testname: Optional[str] = None
        for idx, line in enumerate(lines):
            if testname is None:
                m_t = re.search(r"\+UVM_TESTNAME=([^\s]+)", line)
                if m_t:
                    testname = m_t.group(1)
            if seed is None:
                m_s = re.search(r"\+ntb_random_seed=(\d+)", line)
                if m_s:
                    seed = m_s.group(1)
            if _ERR_RE.search(line) and len(hits) < max_hits:
                m_time = _TIME_RE.search(line)
                lo = max(0, idx - context_lines)
                hi = min(len(lines), idx + context_lines + 1)
                hits.append({
                    "line": idx + 1,
                    "sim_time": m_time.group(1) if m_time else None,
                    "signature": line.strip()[:200],
                    "window": "\n".join(lines[lo:hi]),
                })
        result = {
            "adapter": "vcs",
            "mode": "mock" if self.config.adapter_mode == "mock" else "real",
            "log_path": log_path,
            "test": testname,
            "seed": seed,
            "total_lines": len(lines),
            "first_failure": hits[0] if hits else None,
            "error_count": len(hits),
            "hits": hits,
        }
        self.logger.log("vcs_parse_log", log_path=log_path, error_count=len(hits))
        return result

    def run_simulation(self, test: str, seed: int = 1, dump_fsdb: bool = False,
                       dry_run: Optional[bool] = None, approval_id: Optional[str] = None) -> Dict[str, Any]:
        is_dry = self.config.dry_run if dry_run is None else bool(dry_run)
        cmd = f"make -C $DV_PROJECT_ROOT/verif/sim run TEST={test} SEED={int(seed)} DUMP_FSDB={1 if dump_fsdb else 0}"
        details = {"test": test, "seed": int(seed), "dump_fsdb": bool(dump_fsdb), "command": cmd}
        if is_dry:
            self.logger.log("vcs_sim_dry_run", **details)
            return {"status": "dry_run", "command": cmd, "requires_approval": True}
        decision = self._require_approval("simulation", f"Run VCS simulation {test} (seed={seed})", details, approval_id)
        mock = self.is_mock
        self.logger.log("vcs_sim_executed", mock=mock, approver=decision.approver, **details)
        return {
            "status": "simulated_mock" if mock else "executed",
            "approved_by": decision.approver,
            "command": cmd,
            "log_path": f"sim_out/{test}_{seed}/simv.log",
        }

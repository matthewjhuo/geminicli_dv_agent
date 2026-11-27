"""Synopsys Verdi adapter (read-only debug window planning + approval-gated FSDB extraction)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import BaseAdapter


def _parse_fsdb_table(stdout: str, fallback_signals: List[str]) -> List[Dict[str, Any]]:
    lines = [ln.strip() for ln in (stdout or "").splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        return []
    header_tokens = lines[0].split()
    has_header = any(not tok.replace(".", "", 1).isdigit() for tok in header_tokens)
    if has_header:
        col_names = header_tokens
        data_lines = lines[1:]
    else:
        col_names = ["time_ns"] + list(fallback_signals)
        data_lines = lines

    samples: List[Dict[str, Any]] = []
    for row in data_lines:
        parts = row.split()
        if not parts:
            continue
        item: Dict[str, Any] = {}
        for idx, val in enumerate(parts):
            col = col_names[idx] if idx < len(col_names) else f"col_{idx}"
            if idx == 0 and col.lower().startswith("time"):
                try:
                    item["time_ns"] = int(float(val))
                except ValueError:
                    item["time_ns"] = val
            else:
                try:
                    item[col] = int(val)
                except ValueError:
                    item[col] = val
        samples.append(item)
    return samples


class VerdiAdapter(BaseAdapter):
    name = "verdi"

    def suggest_window(self, hierarchy: str, center_time_ns: int, pre_ns: int = 50, post_ns: int = 20) -> Dict[str, Any]:
        _ = self.is_mock
        infra = self._load_mock_infra().get("verdi", {})
        signals = infra.get("hierarchy_signals", {}).get(
            hierarchy,
            [f"{hierarchy}.clk", f"{hierarchy}.rst_n", f"{hierarchy}.m_valid", f"{hierarchy}.m_ready", f"{hierarchy}.m_data"],
        )
        res = {
            "adapter": "verdi",
            "mode": "mock" if self.config.adapter_mode == "mock" else "real",
            "hierarchy": hierarchy,
            "window_ns": [max(0, center_time_ns - pre_ns), center_time_ns + post_ns],
            "recommended_signals": signals,
        }
        self.logger.log("verdi_suggest_window", hierarchy=hierarchy, window_ns=res["window_ns"])
        return res

    def _build_fsdb_argv(self, fsdb_rel: str, signals: List[str], start_ns: int, end_ns: int) -> List[str]:
        acfg = self._adapter_cfg()
        fsdb_bin = str(acfg.get("fsdbreport_bin") or "fsdbreport")
        argv = [fsdb_bin, fsdb_rel, "-bt", f"{int(start_ns)}ns", "-et", f"{int(end_ns)}ns"]
        for sig in signals:
            argv.extend(["-s", str(sig)])
        return argv

    def extract_fsdb_slice(
        self,
        fsdb_path: str,
        signals: List[str],
        start_ns: int,
        end_ns: int,
        dry_run: Optional[bool] = None,
        approval_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved = self._resolve_project_file(fsdb_path)
        rel_fsdb = resolved.relative_to(self.config.project_root).as_posix()
        is_dry = self.config.dry_run if dry_run is None else bool(dry_run)
        details = {"fsdb_path": rel_fsdb, "signals": list(signals), "start_ns": int(start_ns), "end_ns": int(end_ns)}
        if is_dry:
            return {"status": "dry_run", "requires_approval": True, **details}
        decision = self._require_approval(
            "waveform_extraction",
            f"Extract Verdi FSDB slice {rel_fsdb} [{start_ns}ns..{end_ns}ns]",
            details,
            approval_id,
        )
        mock = self.is_mock
        if mock:
            infra = self._load_mock_infra().get("verdi", {})
            samples = infra.get("waveform_samples", [
                {"time_ns": 1240, "soft_clr": 0, "m_valid": 1, "m_ready": 0, "m_data": "0xdeadbeef"},
                {"time_ns": 1250, "soft_clr": 1, "m_valid": 1, "m_ready": 0, "m_data": "0xdeadbeef"},
                {"time_ns": 1260, "soft_clr": 0, "m_valid": 0, "m_ready": 0, "m_data": "0x00000000"},
            ])
            self.logger.log("verdi_extract_fsdb", mock=True, approver=decision.approver, fsdb_path=rel_fsdb)
            return {"status": "mock_slice", "approved_by": decision.approver, "samples": samples}

        argv = self._build_fsdb_argv(rel_fsdb, list(signals), int(start_ns), int(end_ns))
        res = self._run_command(argv)
        samples = _parse_fsdb_table(res.stdout, list(signals))
        self.logger.log("verdi_extract_fsdb", mock=False, approver=decision.approver, fsdb_path=rel_fsdb, samples=len(samples))
        return {
            "status": "extracted" if res.returncode == 0 and not res.timed_out else "failed",
            "approved_by": decision.approver,
            "argv": res.argv,
            "returncode": res.returncode,
            "timed_out": res.timed_out,
            "samples": samples,
            "stderr": res.stderr,
        }

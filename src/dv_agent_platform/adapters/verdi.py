"""Synopsys Verdi adapter (read-only debug window planning + approval-gated FSDB extraction)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import BaseAdapter


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

    def extract_fsdb_slice(self, fsdb_path: str, signals: List[str], start_ns: int, end_ns: int,
                           dry_run: Optional[bool] = None, approval_id: Optional[str] = None) -> Dict[str, Any]:
        is_dry = self.config.dry_run if dry_run is None else bool(dry_run)
        details = {"fsdb_path": fsdb_path, "signals": list(signals), "start_ns": start_ns, "end_ns": end_ns}
        if is_dry:
            return {"status": "dry_run", "requires_approval": True, **details}
        decision = self._require_approval(
            "waveform_extraction",
            f"Extract Verdi FSDB slice {fsdb_path} [{start_ns}ns..{end_ns}ns]",
            details,
            approval_id,
        )
        mock = self.is_mock
        infra = self._load_mock_infra().get("verdi", {})
        samples = infra.get("waveform_samples", [
            {"time_ns": 1240, "soft_clr": 0, "m_valid": 1, "m_ready": 0, "m_data": "0xdeadbeef"},
            {"time_ns": 1250, "soft_clr": 1, "m_valid": 1, "m_ready": 0, "m_data": "0xdeadbeef"},
            {"time_ns": 1260, "soft_clr": 0, "m_valid": 0, "m_ready": 0, "m_data": "0x00000000"},
        ])
        self.logger.log("verdi_extract_fsdb", mock=mock, approver=decision.approver, fsdb_path=fsdb_path)
        return {"status": "mock_slice" if mock else "extracted", "approved_by": decision.approver, "samples": samples}

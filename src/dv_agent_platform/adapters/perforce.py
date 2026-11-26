"""Perforce (p4) adapter.

HARD INVARIANT: ``submit``, ``push``, ``obliterate``, and destructive ``revert`` are
permanently blocked in code (raise ``ForbiddenOperation``) regardless of approvals.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..errors import ForbiddenOperation
from .base import BaseAdapter


class PerforceAdapter(BaseAdapter):
    name = "perforce"

    def status(self) -> Dict[str, Any]:
        _ = self.is_mock
        data = self._load_mock_infra().get("perforce", {})
        self.logger.log("p4_status")
        return {"client": data.get("client", "${P4CLIENT}"), "opened": data.get("opened", [])}

    def changes(self, path_pattern: str = "//...", max_count: int = 10) -> List[Dict[str, Any]]:
        _ = self.is_mock
        changes = self._load_mock_infra().get("perforce", {}).get("changes", [])
        self.logger.log("p4_changes", path_pattern=path_pattern, count=min(len(changes), max_count))
        return changes[: max(1, min(max_count, 50))]

    def describe(self, changelist: int) -> Dict[str, Any]:
        _ = self.is_mock
        for ch in self._load_mock_infra().get("perforce", {}).get("changes", []):
            if int(ch.get("cl", -1)) == int(changelist):
                self.logger.log("p4_describe", changelist=changelist)
                return ch
        return {"cl": int(changelist), "status": "not_found"}

    def prepare_edit(self, files: List[str], description: str,
                     dry_run: Optional[bool] = None, approval_id: Optional[str] = None) -> Dict[str, Any]:
        is_dry = self.config.dry_run if dry_run is None else bool(dry_run)
        details = {"files": list(files), "description": description}
        if is_dry:
            return {"status": "dry_run", "requires_approval": True, "note": "Human must submit manually", **details}
        decision = self._require_approval("perforce_prepare", f"Open {len(files)} file(s) for p4 edit", details, approval_id)
        mock = self.is_mock
        self.logger.log("p4_prepare_edit", mock=mock, approver=decision.approver, files=files)
        return {
            "status": "pending_changelist_prepared" if mock else "opened",
            "approved_by": decision.approver,
            "files": files,
            "submit_allowed": False,
            "manual_next_step": "Review diff with 'p4 diff' and run 'p4 submit' manually in your terminal.",
        }

    def submit(self, *args: Any, **kwargs: Any) -> None:
        self.logger.log("p4_submit_blocked", level="error")
        raise ForbiddenOperation("Perforce submit is permanently prohibited in Agentic DV Framework; submit manually.")

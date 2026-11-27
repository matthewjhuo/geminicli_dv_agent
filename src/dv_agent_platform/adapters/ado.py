"""Azure DevOps (ADO) requirements & work-item adapter."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import BaseAdapter


class ADOAdapter(BaseAdapter):
    name = "ado"

    def get_work_item(self, item_id: str) -> Dict[str, Any]:
        self._require_mock()
        items = self._load_mock_infra().get("ado", {}).get("work_items", {})
        self.logger.log("ado_get_work_item", item_id=item_id)
        return items.get(str(item_id), {"id": str(item_id), "status": "not_found"})

    def search_requirements(self, query: str = "") -> List[Dict[str, Any]]:
        self._require_mock()
        items = list(self._load_mock_infra().get("ado", {}).get("work_items", {}).values())
        q = (query or "").lower()
        matched = [i for i in items if not q or q in str(i).lower()]
        self.logger.log("ado_search_requirements", query=query, count=len(matched))
        return matched

    def update_work_item(self, item_id: str, comment: str, external_cloud: bool = True,
                         dry_run: Optional[bool] = None, approval_id: Optional[str] = None) -> Dict[str, Any]:
        is_dry = self.config.dry_run if dry_run is None else bool(dry_run)
        details = {"item_id": str(item_id), "comment": comment[:300], "external_cloud": external_cloud}
        if is_dry:
            return {"status": "dry_run", "requires_approval": True, **details}
        self._require_mock()
        self._check_outbound_confidentiality(external_cloud, f"ado://{item_id}")
        decision = self._require_approval("ticket_write", f"Update ADO work item {item_id}", details, approval_id)
        mock = self.is_mock
        self.logger.log("ado_update_work_item", mock=mock, approver=decision.approver, item_id=item_id)
        return {"status": "updated_mock" if mock else "updated", "approved_by": decision.approver, "item_id": str(item_id)}

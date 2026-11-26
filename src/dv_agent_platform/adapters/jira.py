"""JIRA defect & issue tracking adapter."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import BaseAdapter


class JIRAAdapter(BaseAdapter):
    name = "jira"

    def get_issue(self, key: str) -> Dict[str, Any]:
        _ = self.is_mock
        issues = self._load_mock_infra().get("jira", {}).get("issues", {})
        self.logger.log("jira_get_issue", key=key)
        return issues.get(key, {"key": key, "status": "not_found"})

    def search_issues(self, query: str = "") -> List[Dict[str, Any]]:
        _ = self.is_mock
        issues = list(self._load_mock_infra().get("jira", {}).get("issues", {}).values())
        q = (query or "").lower()
        matched = [i for i in issues if not q or q in str(i).lower()]
        self.logger.log("jira_search_issues", query=query, count=len(matched))
        return matched

    def file_defect(self, project_key: str, summary: str, description: str, external_cloud: bool = True,
                    dry_run: Optional[bool] = None, approval_id: Optional[str] = None) -> Dict[str, Any]:
        is_dry = self.config.dry_run if dry_run is None else bool(dry_run)
        details = {"project_key": project_key, "summary": summary, "external_cloud": external_cloud}
        if is_dry:
            return {"status": "dry_run", "requires_approval": True, **details}
        self._check_outbound_confidentiality(external_cloud, f"jira://{project_key}")
        decision = self._require_approval("ticket_write", f"File JIRA defect in {project_key}: {summary}", details, approval_id)
        mock = self.is_mock
        self.logger.log("jira_file_defect", mock=mock, approver=decision.approver, project_key=project_key)
        return {"status": "created_mock" if mock else "created", "key": f"{project_key}-999", "approved_by": decision.approver}

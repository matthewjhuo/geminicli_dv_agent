"""Perforce (p4) adapter.

HARD INVARIANT: ``submit``, ``push``, ``obliterate``, and destructive ``revert`` are
permanently blocked in code (raise ``ForbiddenOperation``) regardless of approvals.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from ..errors import ForbiddenOperation
from .base import BaseAdapter


def _parse_ztag(stdout: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    for raw_line in (stdout or "").splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                records.append(current)
                current = {}
            continue
        if line.startswith("... "):
            rest = line[4:]
            key, _, val = rest.partition(" ")
            key = key.strip()
            val = val.strip()
            if key in current:
                records.append(current)
                current = {}
            current[key] = val
    if current:
        records.append(current)
    return records


class PerforceAdapter(BaseAdapter):
    name = "perforce"

    def _p4_bin(self) -> str:
        return str(self._adapter_cfg().get("bin") or "p4")

    def _validate_local_file(self, path_str: str) -> str:
        if path_str.startswith("//"):
            return path_str
        resolved = self._resolve_project_file(path_str)
        return resolved.relative_to(self.config.project_root).as_posix()

    def status(self) -> Dict[str, Any]:
        mock = self.is_mock
        if mock:
            data = self._load_mock_infra().get("perforce", {})
            self.logger.log("p4_status", mock=True)
            return {"client": data.get("client", "${P4CLIENT}"), "opened": data.get("opened", [])}
        info_res = self._run_command([self._p4_bin(), "-ztag", "info"])
        opened_res = self._run_command([self._p4_bin(), "-ztag", "opened"])
        info_recs = _parse_ztag(info_res.stdout)
        info = info_recs[0] if info_recs else {}
        opened = _parse_ztag(opened_res.stdout)
        self.logger.log("p4_status", mock=False, opened_count=len(opened))
        return {
            "client": info.get("clientName", "${P4CLIENT}"),
            "user": info.get("userName"),
            "opened": opened,
            "returncode": max(info_res.returncode, opened_res.returncode),
        }

    def changes(self, path_pattern: str = "//...", max_count: int = 10, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        count = max(1, min(int(limit if limit is not None else max_count), 50))
        mock = self.is_mock
        if mock:
            changes = self._load_mock_infra().get("perforce", {}).get("changes", [])
            self.logger.log("p4_changes", mock=True, path_pattern=path_pattern, count=min(len(changes), count))
            return changes[:count]
        res = self._run_command([self._p4_bin(), "-ztag", "changes", "-m", str(count), path_pattern])
        recs = _parse_ztag(res.stdout)
        out: List[Dict[str, Any]] = []
        for r in recs:
            item = dict(r)
            if "change" in item and "cl" not in item:
                try:
                    item["cl"] = int(item["change"])
                except ValueError:
                    item["cl"] = item["change"]
            out.append(item)
        self.logger.log("p4_changes", mock=False, path_pattern=path_pattern, count=len(out))
        return out

    def describe(self, changelist: int) -> Dict[str, Any]:
        cl_num = int(changelist)
        mock = self.is_mock
        if mock:
            for ch in self._load_mock_infra().get("perforce", {}).get("changes", []):
                if int(ch.get("cl", -1)) == cl_num:
                    self.logger.log("p4_describe", mock=True, changelist=cl_num)
                    return ch
            return {"cl": cl_num, "status": "not_found"}
        res = self._run_command([self._p4_bin(), "-ztag", "describe", "-s", str(cl_num)])
        recs = _parse_ztag(res.stdout)
        if not recs:
            return {"cl": cl_num, "status": "not_found", "returncode": res.returncode}
        entry = dict(recs[0])
        entry["cl"] = int(entry.get("change", cl_num))
        entry["returncode"] = res.returncode
        self.logger.log("p4_describe", mock=False, changelist=cl_num)
        return entry

    def diff(self, files: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        checked = [self._validate_local_file(f) for f in (files or [])]
        mock = self.is_mock
        if mock:
            self.logger.log("p4_diff", mock=True, files=checked)
            return {"adapter": "perforce", "mode": "mock", "files": checked, "diff": ""}
        argv = [self._p4_bin(), "diff", "-du"] + checked
        res = self._run_command(argv)
        self.logger.log("p4_diff", mock=False, files=checked, returncode=res.returncode)
        return {"adapter": "perforce", "mode": "real", "files": checked, "diff": res.stdout, "returncode": res.returncode}

    def filelog(self, path: str, limit: int = 10) -> List[Dict[str, Any]]:
        checked = self._validate_local_file(path)
        count = max(1, min(int(limit), 50))
        mock = self.is_mock
        if mock:
            changes = self._load_mock_infra().get("perforce", {}).get("changes", [])
            self.logger.log("p4_filelog", mock=True, path=checked)
            return changes[:count]
        res = self._run_command([self._p4_bin(), "-ztag", "filelog", "-m", str(count), checked])
        recs = _parse_ztag(res.stdout)
        self.logger.log("p4_filelog", mock=False, path=checked, count=len(recs))
        return recs

    def annotate(self, path: str) -> Dict[str, Any]:
        checked = self._validate_local_file(path)
        mock = self.is_mock
        if mock:
            self.logger.log("p4_annotate", mock=True, path=checked)
            return {"adapter": "perforce", "mode": "mock", "path": checked, "lines": []}
        res = self._run_command([self._p4_bin(), "-ztag", "annotate", "-c", checked])
        recs = _parse_ztag(res.stdout)
        self.logger.log("p4_annotate", mock=False, path=checked, records=len(recs))
        return {
            "adapter": "perforce",
            "mode": "real",
            "path": checked,
            "records": recs,
            "raw": res.stdout,
            "returncode": res.returncode,
        }

    def prepare_edit(
        self,
        files: List[str],
        description: str,
        dry_run: Optional[bool] = None,
        approval_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        checked = [self._validate_local_file(f) for f in files]
        is_dry = self.config.dry_run if dry_run is None else bool(dry_run)
        details = {"files": checked, "description": description}
        if is_dry:
            return {"status": "dry_run", "requires_approval": True, "note": "Human must submit manually", **details}
        decision = self._require_approval("perforce_prepare", f"Open {len(checked)} file(s) for p4 edit", details, approval_id)
        mock = self.is_mock
        if mock:
            self.logger.log("p4_prepare_edit", mock=True, approver=decision.approver, files=checked)
            return {
                "status": "pending_changelist_prepared",
                "approved_by": decision.approver,
                "files": checked,
                "submit_allowed": False,
                "manual_next_step": "Review diff with 'p4 diff' and run 'p4 submit' manually in your terminal.",
            }
        res = self._run_command([self._p4_bin(), "edit"] + checked)
        self.logger.log("p4_prepare_edit", mock=False, approver=decision.approver, files=checked, returncode=res.returncode)
        return {
            "status": "opened" if res.returncode == 0 and not res.timed_out else "failed",
            "approved_by": decision.approver,
            "files": checked,
            "returncode": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "submit_allowed": False,
            "manual_next_step": "Review diff with 'p4 diff' and run 'p4 submit' manually in your terminal.",
        }

    def submit(self, *args: Any, **kwargs: Any) -> None:
        self.logger.log("p4_submit_blocked", level="error")
        raise ForbiddenOperation("Perforce submit is permanently prohibited in Agentic DV Framework; submit manually.")

    def push(self, *args: Any, **kwargs: Any) -> None:
        self.logger.log("p4_push_blocked", level="error")
        raise ForbiddenOperation("Perforce push is permanently prohibited in Agentic DV Framework; push manually.")

    def obliterate(self, *args: Any, **kwargs: Any) -> None:
        self.logger.log("p4_obliterate_blocked", level="error")
        raise ForbiddenOperation("Perforce obliterate is permanently prohibited in Agentic DV Framework.")

    def revert(self, *args: Any, **kwargs: Any) -> None:
        self.logger.log("p4_revert_blocked", level="error")
        raise ForbiddenOperation("Perforce destructive revert is permanently prohibited in Agentic DV Framework.")

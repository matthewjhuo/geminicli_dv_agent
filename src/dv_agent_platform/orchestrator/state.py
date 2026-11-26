"""Persistent run state (atomic JSON writes) enabling status queries and resume."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..errors import DVAgentError
from ..security.redact import redact_obj

TASK_STATES = {"pending", "running", "succeeded", "failed", "skipped", "blocked", "planned"}
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def new_run_id(workflow: str) -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{re.sub(r'[^A-Za-z0-9-]', '-', workflow)}-{uuid.uuid4().hex[:6]}"


class StateStore:
    def __init__(self, runs_dir: os.PathLike):
        self.runs_dir = Path(runs_dir)
        self._lock = threading.Lock()

    def _path(self, run_id: str) -> Path:
        if not _RUN_ID_RE.match(run_id):
            raise DVAgentError(f"Invalid run id {run_id!r}")
        return self.runs_dir / run_id / "state.json"

    def create(self, run_id: str, workflow: str, graph: Dict[str, Any], params: Dict[str, Any],
               dry_run: bool) -> Dict[str, Any]:
        state = {
            "schema": "dv-run-state/v1",
            "run_id": run_id,
            "workflow": workflow,
            "created_at": time.time(),
            "updated_at": time.time(),
            "status": "planned" if dry_run else "running",
            "dry_run": dry_run,
            "params": params,
            "graph": graph,
            "tasks": {t["id"]: {"status": "planned" if dry_run else "pending", "attempts": 0,
                                "output": None, "error": None, "feedback": [], "prompt_preview": None}
                      for t in graph["tasks"]},
            "aggregate": None,
            "review_rounds": 0,
            "review_history": [],
            "security_alerts": [],
            "usage": None,
            "notes": [],
        }
        self.save(state)
        return state

    def save(self, state: Dict[str, Any]) -> None:
        state["updated_at"] = time.time()
        path = self._path(state["run_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(redact_obj(state), indent=2, ensure_ascii=False, default=str)
        with self._lock:
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".state-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(data)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

    def load(self, run_id: str) -> Dict[str, Any]:
        path = self._path(run_id)
        if not path.is_file():
            raise DVAgentError(f"Run {run_id} not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def list_runs(self) -> List[Dict[str, Any]]:
        out = []
        if not self.runs_dir.is_dir():
            return out
        for p in sorted(self.runs_dir.glob("*/state.json")):
            try:
                s = json.loads(p.read_text(encoding="utf-8"))
                out.append({"run_id": s["run_id"], "workflow": s["workflow"], "status": s["status"],
                            "updated_at": s["updated_at"]})
            except (OSError, ValueError, KeyError):
                continue
        return out

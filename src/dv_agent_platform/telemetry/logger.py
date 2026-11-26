"""JSONL structured logger with automatic secret redaction (thread-safe)."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..security.redact import redact_obj

LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}


class StructuredLogger:
    def __init__(self, path: Optional[os.PathLike] = None, run_id: Optional[str] = None,
                 component: str = "dv-agent", min_level: str = "info", keep_in_memory: bool = True):
        self.path = Path(path) if path else None
        self.run_id = run_id
        self.component = component
        self.min_level = LEVELS[min_level]
        self.keep_in_memory = keep_in_memory
        self.records: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def child(self, component: str) -> "StructuredLogger":
        c = StructuredLogger(self.path, self.run_id, component, keep_in_memory=False)
        c.min_level = self.min_level
        c.records = self.records
        c._lock = self._lock
        c.keep_in_memory = self.keep_in_memory
        return c

    def log(self, event: str, level: str = "info", **fields: Any) -> Dict[str, Any]:
        if LEVELS.get(level, 20) < self.min_level:
            return {}
        record = {
            "ts": round(time.time(), 3),
            "level": level,
            "component": self.component,
            "run_id": self.run_id,
            "event": event,
        }
        record.update(redact_obj(fields))
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            if self.keep_in_memory:
                self.records.append(record)
            if self.path:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        return record

"""SQLite-backed project-scoped DV memory store.

Memory tiers:
* ``semantic``   – architectural facts, spec invariants, register/protocol rules
* ``episodic``   – past regression failures, debug root-cause episodes
* ``procedural`` – reusable debug playbooks, sequence/constraint closure recipes
* ``evidence``   – verified file/line/revision/log/coverage evidence records

Lifecycle:
    candidate  --(verify_entry via EvidenceVerifier)-->  verified  --(promote_to_confirmed w/ human approval)-->  confirmed
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..errors import PromotionError, ProvenanceError
from ..orchestrator.evidence import EvidenceVerifier
from ..security.redact import redact

MEMORY_KINDS = {"semantic", "episodic", "procedural", "evidence"}
MEMORY_STATUSES = {"candidate", "verified", "confirmed", "rejected", "stale"}
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> List[str]:
    return [w.lower() for w in _WORD_RE.findall(text or "") if len(w) >= 2]


class MemoryStore:
    def __init__(self, db_path: Path, project_root: Path):
        self.db_path = Path(db_path)
        self.project_root = Path(project_root).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.verifier = EvidenceVerifier(self.project_root)
        self._init_db()

    @contextlib.contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    entry_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    verification_note TEXT,
                    approved_by TEXT,
                    approval_id TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_proj_status ON memories(project_id, status, kind)")

    def _enrich_provenance(self, prov_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not prov_list:
            raise ProvenanceError("Every memory entry must include at least one provenance record (source + revision).")
        enriched: List[Dict[str, Any]] = []
        for item in prov_list:
            src = str(item.get("source") or "").strip()
            rev = str(item.get("revision") or "").strip()
            if not src or not rev:
                raise ProvenanceError(f"Provenance requires non-empty 'source' and 'revision'; got {item!r}")
            rec = dict(item)
            if "://" not in src:
                candidate = (Path(src) if Path(src).is_absolute() else self.project_root / src).resolve()
                try:
                    candidate.relative_to(self.project_root)
                except ValueError as exc:
                    raise ProvenanceError(f"Provenance path escapes project_root: {src}") from exc
                if candidate.is_file() and "sha256" not in rec:
                    rec["sha256"] = hashlib.sha256(candidate.read_bytes()).hexdigest()
            enriched.append(rec)
        return enriched

    def add_candidate(
        self,
        project_id: str,
        kind: str,
        title: str,
        content: str,
        provenance: List[Dict[str, Any]],
        tags: Optional[List[str]] = None,
    ) -> str:
        if kind not in MEMORY_KINDS:
            raise ValueError(f"Unknown memory kind {kind!r}; expected one of {sorted(MEMORY_KINDS)}")
        if not project_id.strip():
            raise ValueError("project_id is required")
        enriched_prov = self._enrich_provenance(provenance)
        entry_id = f"mem-{kind[:3]}-{uuid.uuid4().hex[:10]}"
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memories (
                    entry_id, project_id, kind, status, title, content,
                    tags_json, provenance_json, verification_note, approved_by, approval_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'candidate', ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
                """,
                (
                    entry_id,
                    project_id,
                    kind,
                    redact(title),
                    redact(content),
                    json.dumps(list(tags or []), ensure_ascii=False),
                    json.dumps(enriched_prov, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return entry_id

    def get(self, entry_id: str, project_id: Optional[str] = None) -> Dict[str, Any]:
        with self._connect() as conn:
            if project_id:
                row = conn.execute(
                    "SELECT * FROM memories WHERE entry_id = ? AND project_id = ?", (entry_id, project_id)
                ).fetchone()
            else:
                row = conn.execute("SELECT * FROM memories WHERE entry_id = ?", (entry_id,)).fetchone()
        if row is None:
            raise KeyError(f"Memory entry {entry_id!r} not found" + (f" in project {project_id!r}" if project_id else ""))
        return self._row_to_dict(row)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        d["tags"] = json.loads(d.pop("tags_json"))
        d["provenance"] = json.loads(d.pop("provenance_json"))
        return d

    def verify_entry(self, entry_id: str, project_id: str) -> Dict[str, Any]:
        entry = self.get(entry_id, project_id=project_id)
        notes: List[str] = []
        all_ok = True
        for prov in entry["provenance"]:
            status, note = self.verifier.verify(prov)
            notes.append(f"{prov.get('source')}: {status} ({note})")
            if status not in {"verified", "external"}:
                all_ok = False
            # Also require at least one locally verified file or explicit verified external proof
            if status == "external" and not prov.get("verified_external", False):
                all_ok = False

        new_status = "verified" if all_ok else "candidate"
        summary_note = "; ".join(notes)
        with self._connect() as conn:
            conn.execute(
                "UPDATE memories SET status = ?, verification_note = ?, updated_at = ? WHERE entry_id = ? AND project_id = ?",
                (new_status, summary_note, time.time(), entry_id, project_id),
            )
        if not all_ok:
            raise PromotionError(f"Entry {entry_id} failed evidence verification: {summary_note}")
        return self.get(entry_id, project_id=project_id)

    def promote_to_confirmed(
        self,
        entry_id: str,
        project_id: str,
        approver: str,
        approval_id: str,
    ) -> Dict[str, Any]:
        entry = self.get(entry_id, project_id=project_id)
        if entry["status"] != "verified":
            raise PromotionError(
                f"Cannot promote entry {entry_id} from status '{entry['status']}': "
                "entry must pass verify_entry() before human confirmation."
            )
        if not approver or not approver.startswith("human:"):
            raise PromotionError("Human approval ('human:<username>') is mandatory to promote DV memory to confirmed.")
        if not approval_id or not approval_id.strip():
            raise PromotionError("A non-empty approval_id is required for auditability.")

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE memories
                SET status = 'confirmed', approved_by = ?, approval_id = ?, updated_at = ?
                WHERE entry_id = ? AND project_id = ?
                """,
                (approver, approval_id, time.time(), entry_id, project_id),
            )
        return self.get(entry_id, project_id=project_id)

    def check_staleness(self, entry_id: str, project_id: str) -> bool:
        """Returns True (and marks status='stale') if any cited local file's SHA-256 changed."""
        entry = self.get(entry_id, project_id=project_id)
        stale = False
        for prov in entry["provenance"]:
            src = str(prov.get("source", ""))
            expected_sha = prov.get("sha256")
            if "://" in src or not expected_sha:
                continue
            target = (Path(src) if Path(src).is_absolute() else self.project_root / src).resolve()
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected_sha:
                stale = True
                break
        if stale and entry["status"] == "confirmed":
            with self._connect() as conn:
                conn.execute(
                    "UPDATE memories SET status = 'stale', updated_at = ? WHERE entry_id = ? AND project_id = ?",
                    (time.time(), entry_id, project_id),
                )
        return stale

    def retrieve(
        self,
        project_id: str,
        query: str,
        kinds: Optional[List[str]] = None,
        only_confirmed: bool = True,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        statuses = ["confirmed"] if only_confirmed else ["confirmed", "verified", "candidate"]
        placeholders_st = ",".join("?" for _ in statuses)
        params: List[Any] = [project_id, *statuses]
        sql = f"SELECT * FROM memories WHERE project_id = ? AND status IN ({placeholders_st})"
        if kinds:
            placeholders_k = ",".join("?" for _ in kinds)
            sql += f" AND kind IN ({placeholders_k})"
            params.extend(kinds)

        with self._connect() as conn:
            rows = [self._row_to_dict(r) for r in conn.execute(sql, params).fetchall()]

        q_tokens = set(_tokenize(query))
        if not q_tokens:
            return rows[:limit]

        scored: List[tuple[float, Dict[str, Any]]] = []
        for doc in rows:
            doc_tokens = _tokenize(f"{doc['title']} {doc['content']} {' '.join(doc['tags'])}")
            if not doc_tokens:
                continue
            overlap = sum(1 for t in doc_tokens if t in q_tokens)
            if overlap == 0:
                continue
            score = overlap / math.sqrt(len(doc_tokens) + 4.0)
            scored.append((score, doc))
        scored.sort(key=lambda pair: -pair[0])
        return [dict(doc, relevance=round(sc, 4)) for sc, doc in scored[:limit]]

    def format_context(self, project_id: str, query: str, limit: int = 4) -> str:
        items = self.retrieve(project_id=project_id, query=query, only_confirmed=True, limit=limit)
        if not items:
            return ""
        lines = []
        for it in items:
            revs = ", ".join(f"{p['source']}@{p['revision']}" for p in it["provenance"])
            lines.append(f"[{it['kind'].upper()}][{it['entry_id']}] {it['title']}: {it['content']} (Provenance: {revs})")
        return "\n".join(lines)

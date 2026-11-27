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
EDA_VERIFICATION_TOOLS = {"vcs", "verdi", "formal", "urg", "regression", "sim", "lec", "lint"}
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> List[str]:
    return [w.lower() for w in _WORD_RE.findall(text or "") if len(w) >= 2]


def _cosine_token_similarity(query_tokens: List[str], doc_tokens: List[str]) -> float:
    if not query_tokens or not doc_tokens:
        return 0.0
    q_counts: Dict[str, int] = {}
    for t in query_tokens:
        q_counts[t] = q_counts.get(t, 0) + 1
    d_counts: Dict[str, int] = {}
    for t in doc_tokens:
        d_counts[t] = d_counts.get(t, 0) + 1
    dot = sum(q_counts[t] * d_counts[t] for t in q_counts if t in d_counts)
    if dot == 0:
        return 0.0
    q_norm = math.sqrt(sum(v * v for v in q_counts.values()))
    d_norm = math.sqrt(sum(v * v for v in d_counts.values()))
    return dot / (q_norm * d_norm)


class MemoryStore:
    def __init__(self, db_path: Path, project_root: Path, *, mode: str = "evolving"):
        if mode not in {"evolving", "frozen"}:
            raise ValueError(f"mode must be 'evolving' or 'frozen', got {mode!r}")
        self.db_path = Path(db_path)
        self.project_root = Path(project_root).resolve()
        self.mode = mode
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
                    applies_when TEXT NOT NULL DEFAULT '',
                    verification_proof_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            # Safe additive migration for pre-existing memory databases
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
            if "applies_when" not in cols:
                conn.execute("ALTER TABLE memories ADD COLUMN applies_when TEXT NOT NULL DEFAULT ''")
            if "verification_proof_json" not in cols:
                conn.execute("ALTER TABLE memories ADD COLUMN verification_proof_json TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_proj_status ON memories(project_id, status, kind)")

    def _ensure_mutable(self) -> None:
        if self.mode == "frozen":
            raise PromotionError("MemoryStore is in 'frozen' read-only mode; mutations are blocked.")

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
        applies_when: str = "",
        verification_proof: Optional[Dict[str, Any]] = None,
    ) -> str:
        self._ensure_mutable()
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
                    applies_when, verification_proof_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'candidate', ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    project_id,
                    kind,
                    redact(title),
                    redact(content),
                    json.dumps(list(tags or []), ensure_ascii=False),
                    json.dumps(enriched_prov, ensure_ascii=False),
                    redact(applies_when or ""),
                    json.dumps(verification_proof, ensure_ascii=False) if verification_proof is not None else None,
                    now,
                    now,
                ),
            )
        return entry_id

    def distill_procedural_skill(
        self,
        project_id: str,
        title: str,
        *,
        do_rules: List[str],
        avoid_rules: List[str],
        applies_when: str,
        provenance: List[Dict[str, Any]],
        eda_verification: Dict[str, Any],
        tags: Optional[List[str]] = None,
    ) -> str:
        """ChipMEM EDA-PASS-only procedural skill distillation.

        Rejects any skill that lacks a verified deterministic EDA PASS record
        (never admits LLM self-judged or failed runs into procedural memory).
        """
        self._ensure_mutable()
        if not isinstance(eda_verification, dict) or eda_verification.get("passed") is not True:
            raise PromotionError(
                "ChipMEM gate blocked procedural skill distillation: deterministic EDA verification must have passed=True."
            )
        tool = str(eda_verification.get("tool") or "").strip().lower()
        if tool not in EDA_VERIFICATION_TOOLS:
            raise PromotionError(
                f"ChipMEM gate blocked procedural skill distillation: unrecognized EDA tool {tool!r} "
                f"(expected one of {sorted(EDA_VERIFICATION_TOOLS)})."
            )
        if not applies_when or not applies_when.strip():
            raise PromotionError(
                "Procedural skill requires an explicit applicability condition ('applies_when' / [iff <condition>])."
            )
        if not do_rules and not avoid_rules:
            raise ValueError("Procedural skill must provide at least one DO or AVOID rule.")

        sections = [f"## APPLIES_WHEN\n[iff {applies_when.strip()}]"]
        if do_rules:
            sections.append("## DO\n" + "\n".join(f"- {r.strip()}" for r in do_rules if r.strip()))
        if avoid_rules:
            sections.append("## AVOID\n" + "\n".join(f"- {r.strip()}" for r in avoid_rules if r.strip()))
        content = "\n\n".join(sections)

        proof = {
            "tool": tool,
            "passed": True,
            "check_name": str(eda_verification.get("check_name") or ""),
            "artifact": str(eda_verification.get("artifact") or ""),
        }
        return self.add_candidate(
            project_id=project_id,
            kind="procedural",
            title=title,
            content=content,
            provenance=provenance,
            tags=list(tags or []) + ["chipmem_procedural", f"eda_pass:{tool}"],
            applies_when=applies_when.strip(),
            verification_proof=proof,
        )

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
        raw_proof = d.pop("verification_proof_json", None)
        d["verification_proof"] = json.loads(raw_proof) if raw_proof else None
        return d

    def verify_entry(self, entry_id: str, project_id: str) -> Dict[str, Any]:
        self._ensure_mutable()
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
        self._ensure_mutable()
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
        min_similarity: float = 0.0,
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

        q_tok_list = _tokenize(query)
        q_tokens = set(q_tok_list)
        if not q_tokens:
            return [doc for doc in rows if not self.check_staleness(doc["entry_id"], project_id)][:limit]

        scored: List[tuple[float, float, Dict[str, Any]]] = []
        for doc in rows:
            if self.check_staleness(doc["entry_id"], project_id):
                continue
            doc_tokens = _tokenize(
                f"{doc['title']} {doc['content']} {doc.get('applies_when', '')} {' '.join(doc['tags'])}"
            )
            if not doc_tokens:
                continue
            overlap = sum(1 for t in doc_tokens if t in q_tokens)
            if overlap == 0:
                continue
            cosine = _cosine_token_similarity(q_tok_list, doc_tokens)
            if cosine < float(min_similarity):
                continue
            score = overlap / math.sqrt(len(doc_tokens) + 4.0)
            scored.append((score, cosine, doc))
        scored.sort(key=lambda tup: (-tup[0], -tup[1]))
        return [
            dict(doc, relevance=round(sc, 4), similarity=round(cos, 4))
            for sc, cos, doc in scored[:limit]
        ]

    def format_context(self, project_id: str, query: str, limit: int = 4, min_similarity: float = 0.0) -> str:
        items = self.retrieve(
            project_id=project_id,
            query=query,
            only_confirmed=True,
            limit=limit,
            min_similarity=min_similarity,
        )
        if not items:
            return ""
        lines = []
        for it in items:
            revs = ", ".join(f"{p['source']}@{p['revision']}" for p in it["provenance"])
            cond = f" [iff {it['applies_when']}]" if it.get("applies_when") else ""
            lines.append(
                f"[{it['kind'].upper()}][{it['entry_id']}]{cond} {it['title']}: {it['content']} (Provenance: {revs})"
            )
        return "\n".join(lines)

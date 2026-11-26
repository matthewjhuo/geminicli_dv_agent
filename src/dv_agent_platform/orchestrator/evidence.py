"""Evidence verification, aggregation and conflict resolution.

Every cited evidence item is re-checked against the project tree:
* path must resolve inside the project root (no traversal),
* the line range must exist,
* if an ``excerpt`` is given it must appear within the cited lines (whitespace-normalized).
``CONFIRMED`` findings whose evidence fails verification are downgraded to ``HYPOTHESIS``.

Conflict resolution scores each candidate classification:
    weight(epistemic) * confidence * evidence_factor
and requires the winner to lead by ``margin`` (relative), otherwise the result is
``UNRESOLVED_CONFLICT`` and a human decision is required.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

EPISTEMIC_WEIGHT = {"CONFIRMED": 1.0, "HYPOTHESIS": 0.5, "UNKNOWN": 0.1}
EVIDENCE_FACTOR = {"verified": 1.0, "external": 0.6, "unverifiable": 0.4, "none": 0.3,
                   "mismatch": 0.1, "missing": 0.1, "rejected": 0.0}
EXTERNAL_SCHEMES = ("p4://", "jira://", "ado://", "regr://", "verdi://", "urg://", "mcp://")
_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", s or "").strip()


class EvidenceVerifier:
    def __init__(self, project_root: Path, max_file_bytes: int = 50_000_000):
        self.root = Path(project_root).resolve()
        self.max_file_bytes = max_file_bytes
        self._cache: Dict[Path, List[str]] = {}

    def _lines(self, path: Path) -> List[str]:
        if path not in self._cache:
            self._cache[path] = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return self._cache[path]

    def verify(self, ev: Dict[str, Any]) -> Tuple[str, str]:
        source = str(ev.get("source", ""))
        if source.startswith(EXTERNAL_SCHEMES):
            return "external", "external reference not verifiable locally"
        rel = source.split("#", 1)[0]
        candidate = Path(rel)
        target = (candidate if candidate.is_absolute() else self.root / candidate).resolve()
        try:
            target.relative_to(self.root)
        except ValueError:
            return "rejected", "path escapes project root"
        if not target.is_file():
            return "missing", "file not found"
        if target.stat().st_size > self.max_file_bytes:
            return "unverifiable", "file too large to verify"
        lines = self._lines(target)
        ls, le = ev.get("line_start"), ev.get("line_end") or ev.get("line_start")
        if ls is None:
            if ev.get("excerpt"):
                return ("verified", "excerpt found") if _norm(ev["excerpt"]) in _norm("\n".join(lines)) \
                    else ("mismatch", "excerpt not found in file")
            return "unverifiable", "no line range or excerpt"
        try:
            ls, le = int(ls), int(le)
        except (TypeError, ValueError):
            return "mismatch", "invalid line numbers"
        if ls < 1 or le < ls or le > len(lines):
            return "mismatch", f"line range {ls}-{le} outside file ({len(lines)} lines)"
        if ev.get("excerpt"):
            window = _norm("\n".join(lines[ls - 1: le]))
            if _norm(ev["excerpt"]) not in window:
                return "mismatch", "excerpt not present in cited lines"
        return "verified", "line range exists" + (" and excerpt matches" if ev.get("excerpt") else "")


def _finding_evidence_status(statuses: List[str]) -> str:
    if not statuses:
        return "none"
    for s in ("verified", "external", "unverifiable", "mismatch", "missing", "rejected"):
        if s in statuses:
            return s
    return "none"


class EvidenceAggregator:
    def __init__(self, verifier: EvidenceVerifier, margin: float = 0.25):
        self.verifier = verifier
        self.margin = margin

    def aggregate(self, outputs: List[Dict[str, Any]]) -> Dict[str, Any]:
        findings: List[Dict[str, Any]] = []
        votes: Dict[str, float] = {}
        voters: Dict[str, List[str]] = {}
        downgraded: List[str] = []
        alerts: List[str] = []
        open_questions: List[str] = []
        actions: List[Dict[str, Any]] = []
        for out in outputs:
            agent = out.get("agent", "?")
            alerts.extend(f"{agent}: {a}" for a in out.get("security_alerts", []) or [])
            open_questions.extend(f"{agent}: {q}" for q in out.get("open_questions", []) or [])
            actions.extend(dict(a, source_agent=agent) for a in out.get("proposed_actions", []) or [])
            best_score = 0.0
            for f in out.get("findings", []):
                ev_results = []
                for ev in f.get("evidence", []):
                    status, note = self.verifier.verify(ev)
                    ev_results.append({"evidence": ev, "status": status, "note": note})
                ev_status = _finding_evidence_status([r["status"] for r in ev_results])
                epistemic = f.get("epistemic", "UNKNOWN")
                if epistemic == "CONFIRMED" and ev_status not in ("verified",):
                    epistemic = "HYPOTHESIS"
                    downgraded.append(f"{agent}:{f.get('id')}")
                score = EPISTEMIC_WEIGHT.get(epistemic, 0.1) * float(f.get("confidence", 0)) * EVIDENCE_FACTOR[ev_status]
                best_score = max(best_score, score)
                findings.append({
                    "agent": agent, "task_id": out.get("task_id"), "id": f.get("id"),
                    "topic": f.get("topic"), "claim": f.get("claim"),
                    "epistemic_reported": f.get("epistemic"), "epistemic_effective": epistemic,
                    "confidence": f.get("confidence"), "evidence_status": ev_status,
                    "evidence": ev_results, "score": round(score, 4),
                })
            cls = out.get("classification")
            if cls and cls != "NOT_APPLICABLE" and best_score > 0:
                votes[cls] = votes.get(cls, 0.0) + best_score
                voters.setdefault(cls, []).append(agent)
        decision, conflict = self._resolve(votes)
        return {
            "decision": decision,
            "needs_human": decision == "UNRESOLVED_CONFLICT",
            "votes": {k: round(v, 4) for k, v in sorted(votes.items(), key=lambda kv: -kv[1])},
            "voters": voters,
            "conflict": conflict,
            "findings": sorted(findings, key=lambda f: -f["score"]),
            "downgraded_confirmed": downgraded,
            "security_alerts": alerts,
            "open_questions": open_questions,
            "proposed_actions": actions,
        }

    def _resolve(self, votes: Dict[str, float]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        if not votes:
            return None, None
        ranked = sorted(votes.items(), key=lambda kv: -kv[1])
        top_cls, top = ranked[0]
        if len(ranked) == 1:
            return top_cls, None
        second_cls, second = ranked[1]
        rel_margin = (top - second) / top if top else 0.0
        conflict = {"top": top_cls, "runner_up": second_cls, "relative_margin": round(rel_margin, 4),
                    "required_margin": self.margin}
        if rel_margin < self.margin:
            return "UNRESOLVED_CONFLICT", conflict
        return top_cls, conflict

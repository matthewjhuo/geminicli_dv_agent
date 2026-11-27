"""GUARD + WikiSkill closed-loop harness evolution guards.

Combines:
1. **WikiSkill (2608.27454v1)** persistent non-rolling-back knowledge layer:
   - ``wiki/patterns/<pattern_id>.md`` (concise failure/success patterns)
   - ``wiki/index.md`` (one-line PROBLEM + ROOT CAUSE + FIX catalog)
   - ``wiki/logs.md`` (chronological evolution log)
   - ``wiki/skill-impact.md`` (programmatic Accepted/Rejected audit trail — NEVER rolled back)
   - ``PURPOSE.md`` back-link generator for ``.gemini/skills/<skill>/PURPOSE.md``
2. **GUARD (2611.23476)** regret-guided non-regressive self-improvement:
   - Two-layer atomic checklist evaluation (Layer 1 execution/safety gates Layer 2 semantics)
   - Paired Directional Regret (``delta_hurt``, ``delta_gap``, ``priority``) + Mastered Rule Pinning
   - Soft-HURT promotion gate (``delta_mean > hurt``) + bounded deduplicated Rejection Ledger
   - GRPO-style parallel candidate pool ranking (``R_m = perf_m - beta*|Q^-| + gamma*|Q^+|``)
   - Strict ``[iff <precondition>]`` scoping validator & workflow task-signature router
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

_BROAD_ALWAYS_RE = re.compile(r"\[always\]", re.IGNORECASE)
_IFF_TAG_RE = re.compile(r"\[iff\s+[^\]]+\]", re.IGNORECASE)
_WORKFLOW_SIGNATURES: Dict[str, str] = {
    "triage": "wf:triage",
    "testplan": "wf:testplan",
    "coverage": "wf:coverage",
    "formal": "wf:formal",
    "formal-signoff": "wf:formal",
    "ral-audit": "wf:ral-audit",
    "rtl-trace": "wf:rtl-trace",
    "review": "wf:review",
}


def route_task_signature(workflow: str, tools: Optional[Sequence[str]] = None) -> str:
    """Deterministic pre-execution signature routing isolating rules across DV workflow classes."""
    wf_norm = (workflow or "generic").strip().lower()
    base = _WORKFLOW_SIGNATURES.get(wf_norm, f"wf:{wf_norm}")
    if tools:
        t_sig = ",".join(sorted({str(t).strip().lower() for t in tools if str(t).strip()}))
        if t_sig:
            return f"{base}|tools:{t_sig}"
    return base


def validate_guard_scoping(
    base_harness: Mapping[str, str],
    candidate_harness: Mapping[str, str],
) -> List[str]:
    """Validate GUARD scoping invariants on newly added rules in candidate harness diffs.

    Flags:
    1. Explicit broad ``[always]`` tags introduced in candidate edits.
    2. Newly appended bullet rules in ``GEMINI.md`` or ``SKILL.md`` that lack an explicit
       ``[iff <precondition>]`` scoping clause (prevents over-triggering & cross-task interference).
    """
    violations: List[str] = []
    for rel, cand_text in candidate_harness.items():
        if not (rel == "GEMINI.md" or rel.endswith("/SKILL.md")):
            continue
        base_lines = set((base_harness.get(rel) or "").splitlines())
        for raw_line in cand_text.splitlines():
            if raw_line in base_lines:
                continue
            stripped = raw_line.strip()
            if not stripped:
                continue
            if _BROAD_ALWAYS_RE.search(stripped):
                violations.append(
                    f"{rel}: broad '[always]' rule forbidden by GUARD scoping; use '[iff <precondition>]'."
                )
                continue
            # Check newly added imperative bullet rules for [iff ...] scoping
            if stripped.startswith(("- ", "* ")) and not stripped.startswith("- `dv-"):
                if not _IFF_TAG_RE.search(stripped):
                    violations.append(
                        f"{rel}: newly added rule lacks GUARD '[iff <precondition>]' scope: {stripped[:80]}"
                    )
    return violations


def _validate_scores(scores):
    if not isinstance(scores, Mapping):
        raise ValueError("Scores must be a mapping")
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in scores.values()):
        raise ValueError("Scores must be finite numbers between 0 and 1")


def evaluate_two_layer_checklist(
    layer1_predicates: Mapping[str, bool],
    layer2_predicates: Mapping[str, bool],
) -> Dict[str, Any]:
    """GUARD 2-Layer self-review: Layer 1 execution/safety failures zero out Layer 2 semantic credit."""
    if any(type(v) is not bool for v in [*layer1_predicates.values(), *layer2_predicates.values()]):
        raise ValueError("Checklist predicates must be actual booleans")
    l1_results = dict(layer1_predicates)
    l1_passed = all(l1_results.values()) if l1_results else True
    l1_failed = [k for k, ok in l1_results.items() if not ok]

    l2_raw = {k: (1.0 if bool(v) else 0.0) for k, v in layer2_predicates.items()}
    # Gate Layer 2 on Layer 1 to eliminate execution-broken false positives
    l2_gated = {k: (val if l1_passed else 0.0) for k, val in l2_raw.items()}
    mean_score = (sum(l2_gated.values()) / len(l2_gated)) if l2_gated else (1.0 if l1_passed else 0.0)

    return {
        "layer1_passed": l1_passed,
        "layer1_failed": l1_failed,
        "layer1": l1_results,
        "layer2_raw": l2_raw,
        "layer2_gated": l2_gated,
        "overall_score": round(mean_score, 4),
    }


def compute_paired_regret(
    prev_scores: Mapping[str, float],
    curr_scores: Mapping[str, float],
    *,
    mastery_threshold: float = 0.999,
) -> Dict[str, Any]:
    """Compute GUARD Paired Directional Regret (``delta_hurt``, ``delta_gap``, ``priority``) & Pinning."""
    _validate_scores(prev_scores)
    _validate_scores(curr_scores)
    all_keys = sorted(set(prev_scores.keys()) | set(curr_scores.keys()))
    per_criterion: Dict[str, Dict[str, Any]] = {}
    regressed: List[str] = []
    unsolved_gap: List[str] = []
    mastered_pinned: List[str] = []

    for c in all_keys:
        p = float(prev_scores.get(c, 0.0))
        cur = float(curr_scores.get(c, 0.0))
        delta_hurt = max(0.0, p - cur)
        delta_gap = max(0.0, 1.0 - cur)
        priority = delta_hurt + delta_gap
        if delta_hurt > 1e-9:
            bucket = "regressed"
            regressed.append(c)
        elif cur >= mastery_threshold:
            bucket = "mastered_pinned"
            mastered_pinned.append(c)
            delta_gap = 0.0
            priority = 0.0
        else:
            bucket = "unsolved_gap"
            unsolved_gap.append(c)

        per_criterion[c] = {
            "prev": round(p, 4),
            "curr": round(cur, 4),
            "delta_hurt": round(delta_hurt, 4),
            "delta_gap": round(delta_gap, 4),
            "priority": round(priority, 4),
            "partition": bucket,
        }

    ranked = sorted(all_keys, key=lambda k: (-per_criterion[k]["priority"], k))
    return {
        "per_criterion": per_criterion,
        "priority_order": ranked,
        "regressed": regressed,
        "unsolved_gap": unsolved_gap,
        "mastered_pinned": mastered_pinned,
    }


def evaluate_soft_hurt_gate(
    baseline_scores: Mapping[str, float],
    candidate_scores: Mapping[str, float],
    *,
    pinned_criteria: Optional[Sequence[str]] = None,
    strict_pinning: bool = True,
) -> Dict[str, Any]:
    """GUARD Soft-HURT non-regression promotion gate (promotes iff ``delta_mean > hurt``)."""
    _validate_scores(baseline_scores)
    _validate_scores(candidate_scores)
    if set(baseline_scores) != set(candidate_scores):
        raise ValueError("Baseline and candidate must cover exactly the same criteria")
    keys = sorted(baseline_scores)
    if not keys:
        return {
            "accepted": False,
            "delta_mean": 0.0,
            "hurt": 0.0,
            "gain": 0.0,
            "improved": [],
            "regressed": [],
            "pinned_regressions": [],
            "recommend_iff_repair": False,
            "reason": "empty evaluation set",
        }

    n = float(len(keys))
    base_mean = sum(float(baseline_scores.get(k, 0.0)) for k in keys) / n
    cand_mean = sum(float(candidate_scores.get(k, 0.0)) for k in keys) / n
    delta_mean = cand_mean - base_mean

    improved: List[str] = []
    regressed: List[str] = []
    hurt_sum = 0.0
    gain_sum = 0.0
    for k in keys:
        b = float(baseline_scores.get(k, 0.0))
        c = float(candidate_scores.get(k, 0.0))
        if c > b + 1e-9:
            improved.append(k)
            gain_sum += c - b
        elif c < b - 1e-9:
            regressed.append(k)
            hurt_sum += b - c

    hurt = hurt_sum / n
    gain = gain_sum / n
    pinned_set = set(pinned_criteria or [k for k in keys if baseline_scores[k] >= 0.999])
    pinned_regressions = [k for k in regressed if k in pinned_set]

    if strict_pinning and pinned_regressions:
        accepted = False
        reason = f"Rejected: regressed pinned mastered criteria {pinned_regressions}"
    elif delta_mean > hurt + 1e-9 and delta_mean > 0.0:
        accepted = True
        reason = f"Accepted: net gain delta_mean={delta_mean:.4f} > hurt={hurt:.4f}"
    else:
        accepted = False
        reason = f"Rejected: delta_mean={delta_mean:.4f} did not exceed hurt={hurt:.4f}"

    return {
        "accepted": accepted,
        "baseline_mean": round(base_mean, 4),
        "candidate_mean": round(cand_mean, 4),
        "delta_mean": round(delta_mean, 4),
        "hurt": round(hurt, 4),
        "gain": round(gain, 4),
        "improved": improved,
        "regressed": regressed,
        "pinned_regressions": pinned_regressions,
        "recommend_iff_repair": (not accepted and len(regressed) == 1 and len(improved) >= 1),
        "reason": reason,
    }


def rank_candidate_pool(
    baseline_scores: Mapping[str, float],
    candidates: Mapping[str, Mapping[str, float]],
    *,
    beta: float = 1.5,
    gamma: float = 0.5,
) -> Dict[str, Any]:
    """GUARD GRPO-style parallel candidate pool ranking (``R_m = perf_m - beta*|Q^-| + gamma*|Q^+|``)."""
    if beta <= gamma:
        raise ValueError("GUARD requires beta > gamma to penalize regressions strictly more than gains")
    scored: List[Dict[str, Any]] = []
    for cid, cscores in candidates.items():
        gate = evaluate_soft_hurt_gate(baseline_scores, cscores)
        perf = gate["candidate_mean"]
        q_minus = len(gate["regressed"])
        q_plus = len(gate["improved"])
        fitness = perf - beta * q_minus + gamma * q_plus
        scored.append(
            {
                "candidate_id": cid,
                "fitness": round(fitness, 4),
                "perf": perf,
                "improved_count": q_plus,
                "regressed_count": q_minus,
                "gate": gate,
            }
        )
    scored.sort(key=lambda item: (-item["fitness"], -item["perf"], item["candidate_id"]))
    return {
        "ranking": scored,
        "best": scored[0] if scored else None,
        "worst": scored[-1] if scored else None,
    }


@dataclass
class WikiPattern:
    pattern_id: str
    problem: str
    root_cause: str
    fix: str
    applies_when: str
    task_signature: str = "wf:generic"
    evidence_refs: List[str] = field(default_factory=list)


class WikiSkillLedger:
    """Local persistent wiki and rejection ledger; ordinary files, not an immutable store."""

    def __init__(self, wiki_root: Path | str, *, max_rejected: int = 100):
        self.wiki_root = Path(wiki_root)
        self.patterns_dir = self.wiki_root / "patterns"
        self.index_path = self.wiki_root / "index.md"
        self.logs_path = self.wiki_root / "logs.md"
        self.impact_path = self.wiki_root / "skill-impact.md"
        self.rejection_ledger_path = self.wiki_root / "rejection_ledger.json"
        self.max_rejected = max(1, int(max_rejected))
        self._init_layout()

    def _init_layout(self) -> None:
        self.patterns_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_path.is_file():
            self.index_path.write_text("# DV WikiSkill Pattern Index\n\n", encoding="utf-8")
        if not self.logs_path.is_file():
            self.logs_path.write_text("# DV WikiSkill Evolution Log\n\n", encoding="utf-8")
        if not self.impact_path.is_file():
            self.impact_path.write_text(
                "# Skill Impact Audit Trail (Never Rolled Back)\n\n"
                "| Timestamp | Candidate | Signature | Outcome | Delta Mean | Hurt | Reason |\n"
                "|---|---|---|---|---|---|---|\n",
                encoding="utf-8",
            )
        if not self.rejection_ledger_path.is_file():
            self.rejection_ledger_path.write_text(json.dumps({"rejected": []}, indent=2) + "\n", encoding="utf-8")

    def record_pattern(self, pattern: WikiPattern) -> Path:
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", pattern.pattern_id).strip("_") or "pattern"
        p_file = self.patterns_dir / f"{safe_id}.md"
        body = (
            f"# Pattern: {safe_id}\n\n"
            f"- **Task Signature**: `{pattern.task_signature}`\n"
            f"- **Applies When**: `[iff {pattern.applies_when}]`\n"
            f"- **Problem**: {pattern.problem}\n"
            f"- **Root Cause**: {pattern.root_cause}\n"
            f"- **Fix**: {pattern.fix}\n"
        )
        if pattern.evidence_refs:
            body += "- **Evidence**: " + ", ".join(f"`{r}`" for r in pattern.evidence_refs) + "\n"
        p_file.write_text(body, encoding="utf-8")

        # Update index.md deduplicated by pattern_id
        idx_line = (
            f"- `{safe_id}` (`{pattern.task_signature}`): "
            f"PROBLEM: {pattern.problem} | ROOT CAUSE: {pattern.root_cause} | FIX: {pattern.fix}\n"
        )
        existing = self.index_path.read_text(encoding="utf-8").splitlines(keepends=True)
        filtered = [ln for ln in existing if not ln.startswith(f"- `{safe_id}` ")]
        filtered.append(idx_line)
        self.index_path.write_text("".join(filtered), encoding="utf-8")

        with self.logs_path.open("a", encoding="utf-8") as fh:
            fh.write(f"- [{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] Recorded pattern `{safe_id}` ({pattern.task_signature})\n")
        return p_file

    @staticmethod
    def _diff_fingerprint(diff_summary: str, task_signature: str) -> str:
        norm = " ".join((diff_summary or "").lower().split())
        return hashlib.sha256(f"{task_signature}::{norm}".encode("utf-8")).hexdigest()[:16]

    def record_proposal_outcome(
        self,
        candidate_id: str,
        diff_summary: str,
        gate_result: Mapping[str, Any],
        *,
        task_signature: str = "wf:generic",
    ) -> Dict[str, Any]:
        """Append local audit entry to ``skill-impact.md`` and, if rejected, to Rejection Ledger."""
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        accepted = bool(gate_result.get("accepted", False))
        outcome = "Accepted" if accepted else "Rejected"
        d_mean = gate_result.get("delta_mean", 0.0)
        hurt = gate_result.get("hurt", 0.0)
        reason = str(gate_result.get("reason") or "").replace("|", "/")

        with self.impact_path.open("a", encoding="utf-8") as fh:
            fh.write(
                f"| {ts} | `{candidate_id}` | `{task_signature}` | **{outcome}** | "
                f"{d_mean:+.4f} | {hurt:.4f} | {reason} |\n"
            )
        with self.logs_path.open("a", encoding="utf-8") as fh:
            fh.write(f"- [{ts}] Proposal `{candidate_id}` ({task_signature}): {outcome} — {diff_summary[:120]}\n")

        fp = self._diff_fingerprint(diff_summary, task_signature)
        if not accepted:
            ledger = json.loads(self.rejection_ledger_path.read_text(encoding="utf-8"))
            items: List[Dict[str, Any]] = list(ledger.get("rejected") or [])
            # Deduplicate by fingerprint
            items = [it for it in items if it.get("fingerprint") != fp]
            items.append(
                {
                    "fingerprint": fp,
                    "timestamp": ts,
                    "candidate_id": candidate_id,
                    "task_signature": task_signature,
                    "diff_summary": diff_summary[:400],
                    "regressed": list(gate_result.get("regressed") or []),
                    "reason": reason,
                }
            )
            items = items[-self.max_rejected :]
            self.rejection_ledger_path.write_text(
                json.dumps({"rejected": items}, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        return {"outcome": outcome, "fingerprint": fp, "accepted": accepted}

    def is_known_rejected(self, diff_summary: str, *, task_signature: str = "wf:generic") -> bool:
        fp = self._diff_fingerprint(diff_summary, task_signature)
        ledger = json.loads(self.rejection_ledger_path.read_text(encoding="utf-8"))
        return any(it.get("fingerprint") == fp for it in (ledger.get("rejected") or []))

    def list_rejected(self, task_signature: Optional[str] = None) -> List[Dict[str, Any]]:
        ledger = json.loads(self.rejection_ledger_path.read_text(encoding="utf-8"))
        items = list(ledger.get("rejected") or [])
        if task_signature:
            items = [it for it in items if it.get("task_signature") == task_signature]
        return items

    @staticmethod
    def write_skill_purpose(skill_dir: Path, skill_name: str, pattern_ids: Sequence[str], rationale: str) -> Path:
        """Write WikiSkill ``PURPOSE.md`` linking a skill back to motivating wiki patterns."""
        skill_dir.mkdir(parents=True, exist_ok=True)
        purpose_path = skill_dir / "PURPOSE.md"
        lines = [
            f"# Purpose: {skill_name}\n\n",
            f"{rationale.strip()}\n\n",
            "## Motivating Wiki Patterns\n",
        ]
        for pid in pattern_ids:
            lines.append(f"- `wiki/patterns/{pid}.md`\n")
        purpose_path.write_text("".join(lines), encoding="utf-8")
        return purpose_path

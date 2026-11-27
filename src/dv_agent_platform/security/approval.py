"""Explicit human approval for simulations, source modifications and other gated actions.

Approvers:
* ``DenyAllApprover``      – default for non-interactive runs; everything gated is refused.
* ``InteractiveApprover``  – human must type ``approve <action_id>`` on a TTY.
* ``ApprovalFileApprover`` – reads a human-maintained JSON file (path from ``$DV_APPROVAL_FILE``).
  Entries must name a human approver; identities starting with ``agent:`` are rejected.
  The file location must be outside paths writable by agents (enforced by policy + hook).
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..errors import ApprovalRequired

GATED_CATEGORIES = {
    "memory_promotion",    # trusted entry/project-scoped human confirmation
    "simulation",          # VCS compile/run, regression launch
    "waveform_extraction", # Verdi batch (license consumption)
    "coverage",            # URG report generation
    "formal",              # formal proof runs
    "source_modification", # RTL/TB/SVA/script edits
    "perforce_prepare",    # p4 edit/add/change (never submit)
    "ticket_write",        # ADO/JIRA create/comment
}


@dataclass
class ApprovalRequest:
    category: str
    summary: str
    details: Dict[str, Any] = field(default_factory=dict)
    action_id: str = field(default_factory=lambda: "act-" + uuid.uuid4().hex[:10])

    def __post_init__(self) -> None:
        if self.category not in GATED_CATEGORIES:
            raise ValueError(f"Unknown approval category {self.category!r}")


@dataclass
class ApprovalDecision:
    approved: bool
    approver: Optional[str]
    reason: str
    approval_id: Optional[str] = None
    decided_at: float = field(default_factory=time.time)


class Approver:
    def decide(self, request: ApprovalRequest, approval_id: Optional[str] = None) -> ApprovalDecision:
        raise NotImplementedError


class DenyAllApprover(Approver):
    def decide(self, request, approval_id=None):
        return ApprovalDecision(False, None, "Non-interactive mode: gated actions require explicit human approval.")


class InteractiveApprover(Approver):
    def __init__(self, input_fn: Callable[[str], str] = input, identity: Optional[str] = None,
                 out=None, require_tty: bool = True):
        self.input_fn = input_fn
        self.identity = identity or os.environ.get("USER") or os.environ.get("USERNAME") or "unknown-human"
        self.out = out or sys.stderr
        self.require_tty = require_tty

    def decide(self, request, approval_id=None):
        if self.require_tty and not sys.stdin.isatty():
            return ApprovalDecision(False, None, "No TTY available for interactive approval.")
        self.out.write(
            f"\n[APPROVAL REQUIRED] category={request.category} action={request.action_id}\n"
            f"  {request.summary}\n  details={json.dumps(request.details, ensure_ascii=False)[:800]}\n"
            f"Type 'approve {request.action_id}' to approve, anything else to deny: "
        )
        self.out.flush()
        answer = (self.input_fn("") or "").strip()
        if answer == f"approve {request.action_id}":
            return ApprovalDecision(True, f"human:{self.identity}", "Approved interactively", request.action_id)
        return ApprovalDecision(False, None, "Denied interactively")


class ApprovalFileApprover(Approver):
    """JSON format::

        {"approvals": [{"approval_id": "APR-001", "category": "simulation",
                        "approver": "human:alice", "expires_at": 1893456000,
                        "scope": {"test": "fifo_smoke"}}]}
    ``scope`` (optional) must be a subset of the request details.
    """

    def __init__(self, path: os.PathLike, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.clock = clock

    def _load(self) -> List[Dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        items = data.get("approvals", []) if isinstance(data, dict) else []
        return [i for i in items if isinstance(i, dict)]

    def decide(self, request, approval_id=None):
        if not approval_id:
            return ApprovalDecision(False, None, "No approval_id supplied.")
        for entry in self._load():
            if entry.get("approval_id") != approval_id:
                continue
            approver = str(entry.get("approver", ""))
            if not approver or approver.startswith("agent:") or not approver.startswith("human:"):
                return ApprovalDecision(False, None, "Approval must be issued by a 'human:' identity.")
            if entry.get("category") != request.category:
                return ApprovalDecision(False, None, "Approval category mismatch.")
            expires = entry.get("expires_at")
            if expires is not None and float(expires) < self.clock():
                return ApprovalDecision(False, None, "Approval expired.")
            scope = entry.get("scope") or {}
            for key, val in scope.items():
                if str(request.details.get(key)) != str(val):
                    return ApprovalDecision(False, None, f"Approval scope mismatch on '{key}'.")
            return ApprovalDecision(True, approver, "Approved via approval file", approval_id)
        return ApprovalDecision(False, None, f"Unknown approval_id {approval_id!r}.")


class ApprovalGate:
    def __init__(self, approver: Optional[Approver] = None, logger=None):
        self.approver = approver or DenyAllApprover()
        self.logger = logger
        self.history: List[Dict[str, Any]] = []

    def require(self, request: ApprovalRequest, approval_id: Optional[str] = None) -> ApprovalDecision:
        decision = self.approver.decide(request, approval_id)
        record = {"request": asdict(request), "decision": asdict(decision)}
        self.history.append(record)
        if self.logger is not None:
            self.logger.log("approval_decision", category=request.category, action_id=request.action_id,
                            approved=decision.approved, approver=decision.approver, reason=decision.reason)
        if not decision.approved:
            raise ApprovalRequired(
                f"[APPROVAL REQUIRED] {request.category}: {request.summary} ({decision.reason})", request
            )
        return decision


def approver_from_env(env: Optional[Dict[str, str]] = None) -> Approver:
    env = dict(os.environ if env is None else env)
    path = env.get("DV_APPROVAL_FILE")
    if path:
        return ApprovalFileApprover(path)
    return DenyAllApprover()

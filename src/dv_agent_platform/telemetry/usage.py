"""Atomic budget admission, actual usage accounting and cost estimation.

Reservations bound admitted estimates, not a provider's unknown final token count.
Actual overruns are recorded truthfully and stop further work.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from ..errors import BudgetExceeded


def estimate_tokens(text: str) -> int:
    """Rough, provider-agnostic estimate (~4 characters per token)."""
    return max(1, len(text or "") // 4)


@dataclass
class UsageRecord:
    agent: str
    task_id: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    model: Optional[str] = None
    ts: float = field(default_factory=time.time)
    estimated: bool = False


class UsageTracker:
    def __init__(self, token_budget: int, pricing: Optional[Dict[str, float]] = None, logger=None):
        self.token_budget = token_budget
        self.pricing = dict(pricing or {"input_per_1m_usd": 0.0, "output_per_1m_usd": 0.0})
        self.logger = logger
        self.records: List[UsageRecord] = []
        self._reserved = 0
        self._reservations: Dict[str, int] = {}
        self._lock = threading.Lock()

    @property
    def total_tokens(self) -> int:
        return sum(r.input_tokens + r.output_tokens for r in self.records)

    def check_budget(self, estimated_tokens: int) -> None:
        """Read-only check. Use reserve() to admit concurrent work atomically."""
        with self._lock:
            projected = self.total_tokens + self._reserved + estimated_tokens
            if projected > self.token_budget:
                raise BudgetExceeded(
                    f"Token budget exceeded: used={self.total_tokens} reserved={self._reserved} "
                    f"next~{estimated_tokens} budget={self.token_budget}"
                )

    def reserve(self, estimated_tokens: int) -> str:
        if type(estimated_tokens) is not int or estimated_tokens <= 0:
            raise ValueError("Reservation must be a positive integer")
        with self._lock:
            if self.total_tokens + self._reserved + estimated_tokens > self.token_budget:
                raise BudgetExceeded("Token budget has insufficient unreserved capacity")
            reservation_id = uuid.uuid4().hex
            self._reservations[reservation_id] = estimated_tokens
            self._reserved += estimated_tokens
            return reservation_id

    def release(self, reservation_id: Optional[str]) -> None:
        with self._lock:
            self._reserved -= self._reservations.pop(reservation_id, 0)

    def record(self, agent: str, task_id: str, input_tokens: int, output_tokens: int,
               latency_s: float, model: Optional[str] = None, estimated: bool = False,
               reservation_id: Optional[str] = None) -> UsageRecord:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("Token usage cannot be negative")
        rec = UsageRecord(agent, task_id, int(input_tokens), int(output_tokens), float(latency_s), model,
                          estimated=estimated)
        with self._lock:
            self._reserved -= self._reservations.pop(reservation_id, 0)
            self.records.append(rec)
            exceeded = self.total_tokens > self.token_budget
        if self.logger is not None:
            self.logger.log("api_usage", **asdict(rec))
        if exceeded:
            raise BudgetExceeded("Actual provider usage exceeded budget; usage was recorded and further work stopped")
        return rec

    def restore(self, summary: Optional[Dict]) -> None:
        """Restore charged usage on resume; never restore abandoned reservations."""
        summary = summary or {}
        records = summary.get("records")
        if records is None:
            tin = int(summary.get("input_tokens", summary.get("total_tokens", 0)))
            tout = int(summary.get("output_tokens", 0))
            restored = [UsageRecord("prior-run", "prior-run", tin, tout, 0, estimated=True)] if tin + tout else []
        else:
            restored = [UsageRecord(**r) for r in records]
        if any(r.input_tokens < 0 or r.output_tokens < 0 for r in restored):
            raise ValueError("Invalid saved token usage")
        with self._lock:
            self.records = restored
            self._reservations.clear()
            self._reserved = 0

    def cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.pricing.get("input_per_1m_usd", 0.0)
                + output_tokens * self.pricing.get("output_per_1m_usd", 0.0)) / 1_000_000

    def summary(self) -> Dict[str, object]:
        with self._lock:
            records = list(self.records)
            reserved = self._reserved
        tin = sum(r.input_tokens for r in records)
        tout = sum(r.output_tokens for r in records)
        per_agent: Dict[str, Dict[str, float]] = {}
        for r in records:
            a = per_agent.setdefault(r.agent, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "latency_s": 0.0})
            a["calls"] += 1
            a["input_tokens"] += r.input_tokens
            a["output_tokens"] += r.output_tokens
            a["latency_s"] = round(a["latency_s"] + r.latency_s, 3)
        return {
            "calls": len(records),
            "input_tokens": tin,
            "output_tokens": tout,
            "total_tokens": tin + tout,
            "token_budget": self.token_budget,
            "budget_remaining": self.token_budget - tin - tout,
            "reserved_tokens": reserved,
            "available_tokens": self.token_budget - tin - tout - reserved,
            "estimated_cost_usd": round(self.cost_usd(tin, tout), 6),
            "any_estimated": any(r.estimated for r in records),
            "per_agent": per_agent,
            "records": [asdict(r) for r in records],
        }

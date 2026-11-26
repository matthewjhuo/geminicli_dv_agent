"""Task decomposition as a validated DAG with dependency management."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set

from ..errors import GraphError
from .contracts import AGENTS

TASK_KINDS = {"agent", "aggregate", "review"}


@dataclass
class Task:
    id: str
    kind: str = "agent"
    agent: Optional[str] = None
    objective: str = ""
    depends_on: List[str] = field(default_factory=list)
    max_retries: Optional[int] = None
    timeout_s: Optional[int] = None

    def __post_init__(self) -> None:
        if self.kind not in TASK_KINDS:
            raise GraphError(f"Task {self.id}: unknown kind {self.kind!r}")
        if self.kind in {"agent", "review"} and self.agent not in AGENTS:
            raise GraphError(f"Task {self.id}: unknown agent {self.agent!r}")
        if self.kind == "review" and self.agent != "dv-review":
            raise GraphError(f"Task {self.id}: review tasks must use dv-review (independent reviewer)")

    @classmethod
    def from_dict(cls, d: Dict) -> "Task":
        return cls(
            id=d["id"], kind=d.get("kind", "agent"), agent=d.get("agent"),
            objective=d.get("objective", ""), depends_on=list(d.get("depends_on", [])),
            max_retries=d.get("max_retries"), timeout_s=d.get("timeout_s"),
        )


class TaskGraph:
    def __init__(self, tasks: Iterable[Task]):
        self.tasks: Dict[str, Task] = {}
        for t in tasks:
            if t.id in self.tasks:
                raise GraphError(f"Duplicate task id {t.id}")
            self.tasks[t.id] = t
        for t in self.tasks.values():
            for dep in t.depends_on:
                if dep not in self.tasks:
                    raise GraphError(f"Task {t.id} depends on unknown task {dep}")
                if dep == t.id:
                    raise GraphError(f"Task {t.id} depends on itself")
        self._levels = self._compute_levels()
        for t in self.tasks.values():
            if t.kind == "review":
                producers = self.upstream_agents(t.id)
                if "dv-review" in {self.tasks[p].agent for p in producers}:
                    raise GraphError("dv-review must not review its own output")

    def _compute_levels(self) -> List[List[str]]:
        indeg = {tid: len(t.depends_on) for tid, t in self.tasks.items()}
        children: Dict[str, List[str]] = {tid: [] for tid in self.tasks}
        for t in self.tasks.values():
            for dep in t.depends_on:
                children[dep].append(t.id)
        frontier = sorted(tid for tid, d in indeg.items() if d == 0)
        levels: List[List[str]] = []
        seen = 0
        while frontier:
            levels.append(frontier)
            seen += len(frontier)
            nxt: List[str] = []
            for tid in frontier:
                for ch in children[tid]:
                    indeg[ch] -= 1
                    if indeg[ch] == 0:
                        nxt.append(ch)
            frontier = sorted(nxt)
        if seen != len(self.tasks):
            raise GraphError("Task graph contains a cycle")
        return levels

    @property
    def levels(self) -> List[List[str]]:
        """Parallel waves: tasks in the same level have no mutual dependencies."""
        return [list(l) for l in self._levels]

    def upstream(self, task_id: str) -> Set[str]:
        out: Set[str] = set()
        stack = list(self.tasks[task_id].depends_on)
        while stack:
            cur = stack.pop()
            if cur not in out:
                out.add(cur)
                stack.extend(self.tasks[cur].depends_on)
        return out

    def upstream_agents(self, task_id: str) -> List[str]:
        return sorted(t for t in self.upstream(task_id) if self.tasks[t].kind == "agent")

    def to_dict(self) -> Dict:
        return {
            "levels": self.levels,
            "tasks": [
                {"id": t.id, "kind": t.kind, "agent": t.agent, "depends_on": t.depends_on,
                 "objective": t.objective}
                for t in self.tasks.values()
            ],
        }

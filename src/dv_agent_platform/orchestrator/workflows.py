"""Built-in workflow templates (task decomposition + dependencies).

Projects may add JSON templates under ``.gemini/dv/workflows/<name>.json`` with the same
shape: {"description": str, "tasks": [{"id", "kind", "agent", "objective", "depends_on"}]}.
Objectives may reference parameters as ``{param}``; missing values render as
``<UNSPECIFIED:param>`` so agents ask instead of guessing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..errors import GraphError
from .task_graph import Task, TaskGraph

BUILTIN_WORKFLOWS: Dict[str, Dict[str, Any]] = {
    "triage": {
        "description": "Regression failure triage with parallel RTL/TB/SVA investigation.",
        "tasks": [
            {"id": "collect", "agent": "dv-debug",
             "objective": "Extract the first failure signature, seed, sim time and failing component from {log_path}. Test={test}."},
            {"id": "rtl", "agent": "dv-rtl", "depends_on": ["collect"],
             "objective": "Evaluate the RTL-side hypothesis for the failure found by 'collect'. RTL root: {rtl_root}."},
            {"id": "tb", "agent": "dv-uvm", "depends_on": ["collect"],
             "objective": "Mode=debug. Evaluate the testbench-side hypothesis (monitor/scoreboard/sequence) for the failure."},
            {"id": "sva", "agent": "dv-sva", "depends_on": ["collect"],
             "objective": "Determine whether any failing or related assertion is an SVA_DEFECT or a real design/TB issue."},
            {"id": "aggregate", "kind": "aggregate", "depends_on": ["rtl", "tb", "sva"]},
            {"id": "review", "kind": "review", "agent": "dv-review", "depends_on": ["aggregate"],
             "objective": "Independently verify the aggregated root-cause conclusion and its evidence."},
        ],
    },
    "plan-feature": {
        "description": "Spec → architecture → parallel UVM/SVA/coverage planning → review.",
        "tasks": [
            {"id": "spec", "agent": "dv-spec", "objective": "Extract requirements and gaps for {feature} from {spec_path}."},
            {"id": "arch", "agent": "dv-architect", "depends_on": ["spec"],
             "objective": "Build the verification strategy and test matrix for {feature}."},
            {"id": "uvm", "agent": "dv-uvm", "depends_on": ["arch"],
             "objective": "Mode=review. Propose UVM components/sequences (proposals only, no writes)."},
            {"id": "sva", "agent": "dv-sva", "depends_on": ["arch"],
             "objective": "Propose an assertion plan with bind strategy (proposals only, no writes)."},
            {"id": "cov", "agent": "dv-coverage", "depends_on": ["arch"],
             "objective": "Propose the functional coverage model and closure targets."},
            {"id": "aggregate", "kind": "aggregate", "depends_on": ["uvm", "sva", "cov"]},
            {"id": "review", "kind": "review", "agent": "dv-review", "depends_on": ["aggregate"],
             "objective": "Review the plan for traceability, completeness and safety."},
        ],
    },
    "coverage-closure": {
        "description": "Coverage hole disposition with formal unreachability and stimulus proposals.",
        "tasks": [
            {"id": "cov", "agent": "dv-coverage", "objective": "Classify coverage holes in {coverage_report}."},
            {"id": "formal", "agent": "dv-formal", "depends_on": ["cov"],
             "objective": "Assess unreachability of holes classified RTL_DEAD_OR_UNREACHABLE_CODE."},
            {"id": "uvm", "agent": "dv-uvm", "depends_on": ["cov"],
             "objective": "Mode=review. Propose targeted sequences/constraints for STIMULUS_CONSTRAINT_GAP holes."},
            {"id": "aggregate", "kind": "aggregate", "depends_on": ["formal", "uvm"]},
            {"id": "review", "kind": "review", "agent": "dv-review", "depends_on": ["aggregate"],
             "objective": "Verify that no waiver lacks a structural/formal justification."},
        ],
    },
    "formal-signoff": {
        "description": "Formal setup audit and assertion quality review.",
        "tasks": [
            {"id": "formal", "agent": "dv-formal", "objective": "Audit formal setup and results for {target}."},
            {"id": "sva", "agent": "dv-sva", "depends_on": ["formal"],
             "objective": "Audit assertion quality, vacuity covers and assumptions for {target}."},
            {"id": "aggregate", "kind": "aggregate", "depends_on": ["formal", "sva"]},
            {"id": "review", "kind": "review", "agent": "dv-review", "depends_on": ["aggregate"],
             "objective": "Confirm sign-off readiness verdict is evidence-backed."},
        ],
    },
}


class _SafeDict(dict):
    def __missing__(self, key):
        return f"<UNSPECIFIED:{key}>"


def render_objective(template: str, params: Dict[str, Any]) -> str:
    try:
        return template.format_map(_SafeDict({k: str(v) for k, v in params.items()}))
    except (ValueError, IndexError):
        return template


def load_workflow(name: str, project_root: Optional[Path] = None) -> Dict[str, Any]:
    if project_root is not None:
        custom = Path(project_root) / ".gemini" / "dv" / "workflows" / f"{name}.json"
        if custom.is_file():
            return json.loads(custom.read_text(encoding="utf-8"))
    if name not in BUILTIN_WORKFLOWS:
        raise GraphError(f"Unknown workflow {name!r}. Available: {sorted(BUILTIN_WORKFLOWS)}")
    return BUILTIN_WORKFLOWS[name]


def build_graph(spec: Dict[str, Any], params: Optional[Dict[str, Any]] = None) -> TaskGraph:
    params = params or {}
    tasks: List[Task] = []
    for d in spec["tasks"]:
        d = dict(d)
        d["objective"] = render_objective(d.get("objective", ""), params)
        tasks.append(Task.from_dict(d))
    return TaskGraph(tasks)

"""Agent output contract (dv-agent-output/v1) parsing and validation.

Mirrors ``project-template/.gemini/dv/contracts/agent_output.schema.json`` without
requiring the third-party ``jsonschema`` package.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from ..errors import ContractViolation

CONTRACT_VERSION = "dv-agent-output/v1"
AGENTS = {
    "dv-spec", "dv-architect", "dv-rtl", "dv-uvm", "dv-sva",
    "dv-debug", "dv-coverage", "dv-formal", "dv-review",
}
STATUSES = {"ok", "needs_input", "blocked", "failed"}
EPISTEMIC = {"CONFIRMED", "HYPOTHESIS", "UNKNOWN"}
CLASSIFICATIONS = {
    "RTL_DEFECT", "TB_DEFECT", "SVA_DEFECT", "SPEC_AMBIGUITY", "ENV_OR_TOOL_ISSUE",
    "INTENTIONAL_NEGATIVE_TEST", "NOT_APPLICABLE",
}
ACTION_TYPES = {"read", "edit_source", "run_simulation", "run_formal", "run_coverage", "file_ticket", "ask_human"}
GATED_ACTIONS = {"edit_source", "run_simulation", "run_formal", "run_coverage", "file_ticket"}
VERDICTS = {"approve", "revise", "reject"}

_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.S)


def extract_json_block(text: str) -> Dict[str, Any]:
    if isinstance(text, dict):
        return text
    candidates = _FENCE_RE.findall(text or "")
    for block in reversed(candidates):
        try:
            obj = json.loads(block)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    stripped = (text or "").strip()
    for attempt in (stripped, stripped[stripped.find("{"): stripped.rfind("}") + 1]):
        if not attempt:
            continue
        try:
            obj = json.loads(attempt)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    raise ContractViolation("No JSON object found in agent response", ["missing_json"])


def validate_agent_output(obj: Any, expected_agent: str = None, expected_task: str = None) -> List[str]:
    errors: List[str] = []
    if not isinstance(obj, dict):
        return ["output is not an object"]
    for key in ("contract_version", "agent", "task_id", "status", "summary", "findings",
                "open_questions", "proposed_actions"):
        if key not in obj:
            errors.append(f"missing required field '{key}'")
    if errors:
        return errors
    if obj["contract_version"] != CONTRACT_VERSION:
        errors.append("contract_version must be " + CONTRACT_VERSION)
    if obj["agent"] not in AGENTS:
        errors.append(f"unknown agent {obj['agent']!r}")
    if expected_agent and obj["agent"] != expected_agent:
        errors.append(f"agent mismatch: expected {expected_agent}, got {obj['agent']}")
    if expected_task and obj["task_id"] != expected_task:
        errors.append(f"task_id mismatch: expected {expected_task}, got {obj['task_id']}")
    if obj["status"] not in STATUSES:
        errors.append(f"invalid status {obj['status']!r}")
    if "classification" in obj and obj["classification"] not in CLASSIFICATIONS:
        errors.append(f"invalid classification {obj['classification']!r}")
    if not isinstance(obj["findings"], list):
        errors.append("findings must be a list")
    else:
        for i, f in enumerate(obj["findings"]):
            if not isinstance(f, dict):
                errors.append(f"findings[{i}] must be an object")
                continue
            for key in ("id", "claim", "epistemic", "confidence", "evidence"):
                if key not in f:
                    errors.append(f"findings[{i}] missing '{key}'")
            if f.get("epistemic") not in EPISTEMIC:
                errors.append(f"findings[{i}].epistemic invalid")
            conf = f.get("confidence")
            if not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
                errors.append(f"findings[{i}].confidence must be within [0,1]")
            ev = f.get("evidence")
            if not isinstance(ev, list):
                errors.append(f"findings[{i}].evidence must be a list")
            else:
                if f.get("epistemic") == "CONFIRMED" and not ev:
                    errors.append(f"findings[{i}] is CONFIRMED but has no evidence")
                for j, e in enumerate(ev):
                    if not isinstance(e, dict) or not e.get("source"):
                        errors.append(f"findings[{i}].evidence[{j}] missing 'source'")
    if not isinstance(obj["open_questions"], list):
        errors.append("open_questions must be a list")
    if not isinstance(obj["proposed_actions"], list):
        errors.append("proposed_actions must be a list")
    else:
        for i, a in enumerate(obj["proposed_actions"]):
            if not isinstance(a, dict):
                errors.append(f"proposed_actions[{i}] must be an object")
                continue
            if a.get("type") not in ACTION_TYPES:
                errors.append(f"proposed_actions[{i}].type invalid")
            if a.get("type") in GATED_ACTIONS and a.get("requires_approval") is not True:
                errors.append(f"proposed_actions[{i}] of type {a.get('type')} must set requires_approval=true")
    if obj.get("agent") == "dv-review" and obj.get("review_verdict") not in VERDICTS:
        errors.append("dv-review output must include review_verdict approve|revise|reject")
    return errors


def parse_and_validate(text: str, expected_agent: str = None, expected_task: str = None) -> Dict[str, Any]:
    obj = extract_json_block(text)
    errors = validate_agent_output(obj, expected_agent, expected_task)
    if errors:
        raise ContractViolation("Agent output violates dv-agent-output/v1", errors)
    return obj

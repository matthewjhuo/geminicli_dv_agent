"""DAG Orchestrator with bounded concurrency, evidence aggregation, independent review,
bounded retry loops, dry-run mode, persistent state, and resume support.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from typing import Any, Dict, List, Optional

from ..config import FrameworkConfig
from ..errors import BudgetExceeded, ContractViolation, DVAgentError, TaskTimeout, BackendError
from ..security.untrusted import detect_injection, wrap_untrusted
from ..telemetry.logger import StructuredLogger
from ..telemetry.usage import UsageTracker, estimate_tokens
from .backends import AgentBackend
from .contracts import parse_and_validate
from .evidence import EvidenceAggregator, EvidenceVerifier
from .state import StateStore, new_run_id
from .task_graph import Task, TaskGraph
from .workflows import build_graph, load_workflow


class Orchestrator:
    def __init__(
        self,
        config: FrameworkConfig,
        backend: Optional[AgentBackend] = None,
        logger: Optional[StructuredLogger] = None,
        memory_context_provider=None,
    ):
        self.config = config
        self.backend = backend
        self.state_store = StateStore(config.runs_dir)
        self.logger = logger or StructuredLogger(config.logs_dir / "orchestrator.jsonl", component="orchestrator")
        self.usage = UsageTracker(config.token_budget, config.pricing, logger=self.logger.child("usage"))
        self.verifier = EvidenceVerifier(config.project_root)
        self.aggregator = EvidenceAggregator(self.verifier, margin=config.conflict_margin)
        self.memory_context_provider = memory_context_provider

    def _build_prompt(
        self,
        task: Task,
        state: Dict[str, Any],
        feedback: Optional[List[str]] = None,
    ) -> str:
        sections: List[str] = [
            f"TASK_ID: {task.id}",
            f"AGENT_ROLE: {task.agent}",
            f"OBJECTIVE: {task.objective}",
            "CONTRACT: Return exactly one fenced json block matching dv-agent-output/v1.",
        ]
        if self.memory_context_provider is not None:
            mem_snippet = self.memory_context_provider(self.config.project_id, task.objective)
            if mem_snippet:
                sections.append("PROJECT_CONFIRMED_MEMORY:\n" + wrap_untrusted(mem_snippet, "memory_store", max_chars=4000))

        upstream_payloads: List[Dict[str, Any]] = []
        for dep_id in task.depends_on:
            dep_state = state["tasks"].get(dep_id, {})
            if dep_id == "aggregate" and state.get("aggregate"):
                upstream_payloads.append({"from": "aggregate", "aggregate": state["aggregate"]})
            elif dep_state.get("output"):
                upstream_payloads.append({"from": dep_id, "output": dep_state["output"]})
        if upstream_payloads:
            raw_up = json.dumps(upstream_payloads, ensure_ascii=False, indent=2)
            hits = detect_injection(raw_up)
            for h in hits:
                alert = f"Injection pattern '{h.kind}' detected in upstream context for {task.id} (line {h.line})"
                if alert not in state["security_alerts"]:
                    state["security_alerts"].append(alert)
            sections.append("UPSTREAM_EVIDENCE:\n" + wrap_untrusted(raw_up, f"upstream:{task.id}", max_chars=12000))

        if feedback:
            sections.append(
                "RETRY_OR_REVIEW_FEEDBACK (address every item with verified evidence):\n- "
                + "\n- ".join(feedback)
            )
        return "\n\n".join(sections)

    def _execute_agent_task(self, task: Task, state: Dict[str, Any]) -> Dict[str, Any]:
        if self.backend is None:
            raise DVAgentError("Cannot execute live/mock agent task when backend is None (enable dry_run or pass backend)")
        tstate = state["tasks"][task.id]
        retries = self.config.max_retries if task.max_retries is None else min(task.max_retries, 3)
        timeout_s = task.timeout_s or self.config.task_timeout_s
        feedback = list(tstate.get("feedback") or [])
        last_err: Optional[Exception] = None

        for attempt in range(1, retries + 2):
            prompt = self._build_prompt(task, state, feedback=feedback if feedback else None)
            tstate["prompt_preview"] = prompt[:600]
            tstate["attempts"] = tstate.get("attempts", 0) + 1
            tstate["status"] = "running"
            self.state_store.save(state)

            est = estimate_tokens(prompt) + 256
            reservation_id = None
            try:
                reservation_id = self.usage.reserve(est)
                res = self.backend.invoke(task.agent, prompt, task.id, timeout_s)
                self.usage.record(
                    task.agent, task.id, res.input_tokens, res.output_tokens,
                    res.latency_s, model=res.model, estimated=res.estimated_tokens,
                    reservation_id=reservation_id,
                )
                inj_hits = detect_injection(res.text)
                for h in inj_hits:
                    alert = f"Injection pattern '{h.kind}' in {task.agent} output (line {h.line})"
                    if alert not in state["security_alerts"]:
                        state["security_alerts"].append(alert)

                parsed = parse_and_validate(res.text, expected_agent=task.agent, expected_task=task.id)
                tstate["output"] = parsed
                tstate["error"] = None
                tstate["status"] = {"ok": "succeeded", "needs_input": "needs_input",
                                    "blocked": "blocked", "failed": "failed"}[parsed["status"]]
                self.logger.log("task_completed", task_id=task.id, agent=task.agent,
                                status=tstate["status"], attempts=tstate["attempts"])
                return parsed
            except BudgetExceeded:
                tstate["status"] = "failed"
                tstate["error"] = "Token budget exceeded"
                raise
            except (ContractViolation, TaskTimeout, BackendError) as exc:
                last_err = exc
                err_msg = f"{type(exc).__name__}: {exc}"
                if isinstance(exc, ContractViolation) and exc.errors:
                    err_msg += f" ({'; '.join(exc.errors)})"
                feedback.append(err_msg)
                tstate["feedback"] = feedback
                tstate["error"] = err_msg
                self.logger.log("task_attempt_failed", level="warning", task_id=task.id,
                                agent=task.agent, attempt=attempt, error=err_msg)
                if attempt > retries:
                    tstate["status"] = "failed"
                    self.state_store.save(state)
                    raise
            finally:
                self.usage.release(reservation_id)

        raise last_err or DVAgentError(f"Task {task.id} failed")

    def _run_wave(self, wave: List[str], graph: TaskGraph, state: Dict[str, Any]) -> None:
        agent_ids: List[str] = []
        for tid in wave:
            task = graph.tasks[tid]
            tstate = state["tasks"][tid]
            if tstate["status"] == "succeeded":
                continue
            if any(state["tasks"][d]["status"] != "succeeded" for d in task.depends_on):
                tstate["status"] = "skipped"
                tstate["error"] = "Skipped due to failed/blocked dependency"
                continue
            if task.kind == "aggregate":
                upstream_outputs = [
                    state["tasks"][u]["output"]
                    for u in graph.upstream_agents(tid)
                    if state["tasks"][u].get("output")
                ]
                agg = self.aggregator.aggregate(upstream_outputs)
                state["aggregate"] = agg
                for a in agg.get("security_alerts", []):
                    if a not in state["security_alerts"]:
                        state["security_alerts"].append(a)
                tstate["output"] = agg
                tstate["status"] = "succeeded"
                tstate["attempts"] = tstate.get("attempts", 0) + 1
                self.logger.log("aggregate_completed", task_id=tid, decision=agg.get("decision"))
            else:
                agent_ids.append(tid)

        if not agent_ids:
            self.state_store.save(state)
            return

        workers = min(self.config.max_concurrency, len(agent_ids))
        errors: List[Exception] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(self._execute_agent_task, graph.tasks[tid], state): tid for tid in agent_ids}
            for fut in as_completed(futs):
                try:
                    fut.result()
                except Exception as exc:  # noqa: BLE001
                    tid = futs[fut]
                    state["tasks"][tid]["status"] = "failed"
                    state["tasks"][tid]["error"] = f"{type(exc).__name__}: {exc}"
                    errors.append(exc)
        self.state_store.save(state)
        if errors:
            raise errors[0]

    def run_graph(
        self,
        workflow_name: str,
        graph: TaskGraph,
        params: Optional[Dict[str, Any]] = None,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        params = dict(params or {})
        rid = run_id or new_run_id(workflow_name)
        self.logger.run_id = rid
        self.usage = UsageTracker(self.config.token_budget, self.config.pricing, logger=self.logger.child("usage"))
        state = self.state_store.create(rid, workflow_name, graph.to_dict(), params, dry_run=self.config.dry_run)
        if self.config.dry_run:
            for tid, task in graph.tasks.items():
                if task.kind in {"agent", "review"}:
                    state["tasks"][tid]["prompt_preview"] = self._build_prompt(task, state)[:600]
            state["usage"] = self.usage.summary()
            self.state_store.save(state)
            self.logger.log("workflow_dry_run", workflow=workflow_name, levels=graph.levels)
            return state
        return self._execute_state(graph, state)

    def run_workflow(self, workflow_name: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        spec = load_workflow(workflow_name, self.config.project_root)
        graph = build_graph(spec, params)
        return self.run_graph(workflow_name, graph, params=params)

    def resume(self, run_id: str) -> Dict[str, Any]:
        state = self.state_store.load(run_id)
        self.logger.run_id = run_id
        tasks = [Task.from_dict(t) for t in state["graph"]["tasks"]]
        graph = TaskGraph(tasks)
        self.usage = UsageTracker(self.config.token_budget, self.config.pricing, logger=self.logger.child("usage"))
        self.usage.restore(state.get("usage"))
        if state["status"] == "succeeded":
            return state
        invalidated = set()
        for tid, tstate in state["tasks"].items():
            if tstate["status"] != "succeeded":
                invalidated.add(tid)
                invalidated.update(graph.downstream(tid))
        for tid in invalidated:
            tstate = state["tasks"][tid]
            tstate["status"] = "pending"
            tstate["error"] = None
            tstate["output"] = None
        if any(graph.tasks[tid].kind == "aggregate" for tid in invalidated):
            state["aggregate"] = None
        state["dry_run"] = False
        state["status"] = "running"
        self.state_store.save(state)
        return self._execute_state(graph, state)

    def _execute_state(self, graph: TaskGraph, state: Dict[str, Any]) -> Dict[str, Any]:
        review_tasks = [t for t in graph.tasks.values() if t.kind == "review"]
        try:
            for round_idx in range(self.config.max_review_rounds + 1):
                for wave in graph.levels:
                    self._run_wave(wave, graph, state)

                incomplete = {tid: task["status"] for tid, task in state["tasks"].items()
                              if task["status"] != "succeeded"}
                if incomplete:
                    values = set(incomplete.values())
                    state["status"] = "failed" if "failed" in values else (
                        "needs_input" if "needs_input" in values else "blocked")
                    state["notes"].append("Required tasks did not complete: " + json.dumps(incomplete, sort_keys=True))
                    break
                if not review_tasks:
                    state["status"] = "needs_human" if (state.get("aggregate") or {}).get("needs_human") else "succeeded"
                    break

                reviews = [state["tasks"][task.id].get("output") or {} for task in review_tasks]
                verdicts = [out.get("review_verdict") for out in reviews]
                if any(v not in {"approve", "revise", "reject"} for v in verdicts):
                    state["status"] = "blocked"
                    state["notes"].append("Required independent review verdict is missing")
                    break
                verdict = "reject" if "reject" in verdicts else ("revise" if "revise" in verdicts else "approve")
                issues = [issue for out in reviews for issue in out.get("review_issues", [])]
                state["review_rounds"] = round_idx + 1
                state["review_history"].append({"round": round_idx + 1, "verdict": verdict, "issues": issues})

                if verdict == "approve":
                    state["status"] = "needs_human" if (state.get("aggregate") or {}).get("needs_human") else "succeeded"
                    break
                if verdict == "reject":
                    state["status"] = "rejected_by_review"
                    break
                if round_idx >= self.config.max_review_rounds:
                    state["status"] = "needs_human"
                    state["notes"].append("Reached max_review_rounds without 'approve'; escalating to human.")
                    break

                # verdict == "revise": reset upstream producer tasks that feed aggregate/review
                producers = sorted({producer for task in review_tasks for producer in graph.upstream_agents(task.id)})
                target_producers = [
                    p for p in producers
                    if any(p in iss or (graph.tasks[p].agent or "") in iss for iss in issues)
                ] or producers
                invalidated = set(target_producers)
                for pid in target_producers:
                    invalidated.update(graph.downstream(pid))
                    state["tasks"][pid]["feedback"] = list(state["tasks"][pid].get("feedback") or []) + (
                        issues or ["Revise findings with verified evidence"]
                    )
                for tid in invalidated:
                    state["tasks"][tid].update(status="pending", output=None, error=None)
                if any(graph.tasks[tid].kind == "aggregate" for tid in invalidated):
                    state["aggregate"] = None
                self.state_store.save(state)
        except Exception as exc:
            state["status"] = "failed"
            state["notes"].append(f"{type(exc).__name__}: {exc}")
            state["usage"] = self.usage.summary()
            self.state_store.save(state)
            raise

        state["usage"] = self.usage.summary()
        self.state_store.save(state)
        return state

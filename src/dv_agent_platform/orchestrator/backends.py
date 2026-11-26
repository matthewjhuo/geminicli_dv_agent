"""Agent execution backends.

* ``MockBackend``      – deterministic scripted responses for tests and demos (no API calls).
* ``GeminiCliBackend`` – invokes the installed Gemini CLI in headless mode
  (``gemini -p ... -o json --approval-mode plan``). ``plan`` is the native read-only
  approval mode, so automated subagent runs cannot edit files or run shell commands.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from ..errors import BackendError, TaskTimeout
from ..telemetry.usage import estimate_tokens


@dataclass
class BackendResult:
    text: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    model: Optional[str] = None
    estimated_tokens: bool = False
    raw: Any = None


class AgentBackend:
    name = "abstract"

    def invoke(self, agent: str, prompt: str, task_id: str, timeout_s: int) -> BackendResult:
        raise NotImplementedError


Response = Union[str, Dict[str, Any], Exception, Callable[[str], Any]]


class MockBackend(AgentBackend):
    """Scripted responses keyed by task id (preferred) or agent name.

    A list of responses is consumed in order (useful for retry/review tests); the last
    entry repeats. A response may be a dict (serialized as a fenced json block), a raw
    string, an Exception instance (raised), or a callable receiving the prompt.
    """

    name = "mock"

    def __init__(self, responses: Dict[str, Union[Response, List[Response]]], delay_s: float = 0.0):
        self.responses = {k: (list(v) if isinstance(v, list) else [v]) for k, v in responses.items()}
        self.delay_s = delay_s
        self.calls: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._active = 0
        self.max_active = 0

    def _next(self, key: str) -> Response:
        with self._lock:
            queue = self.responses[key]
            return queue.pop(0) if len(queue) > 1 else queue[0]

    def invoke(self, agent, prompt, task_id, timeout_s):
        key = task_id if task_id in self.responses else agent
        if key not in self.responses:
            raise BackendError(f"MockBackend has no response for task {task_id} / agent {agent}")
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
            self.calls.append({"agent": agent, "task_id": task_id, "prompt": prompt})
        start = time.time()
        try:
            resp = self._next(key)
            delay = getattr(resp, "delay_s", None) if not isinstance(resp, (str, dict)) else None
            delay = self.delay_s if delay is None else delay
            if delay:
                if delay > timeout_s:
                    time.sleep(min(timeout_s, 0.05))
                    raise TaskTimeout(f"{task_id} exceeded {timeout_s}s")
                time.sleep(delay)
            if isinstance(resp, Exception):
                raise resp
            if callable(resp):
                resp = resp(prompt)
            text = "```json\n" + json.dumps(resp, ensure_ascii=False) + "\n```" if isinstance(resp, dict) else str(resp)
            return BackendResult(text, estimate_tokens(prompt), estimate_tokens(text), time.time() - start,
                                 model="mock", estimated_tokens=True)
        finally:
            with self._lock:
                self._active -= 1


class GeminiCliBackend(AgentBackend):
    name = "gemini-cli"

    def __init__(self, command: Optional[Sequence[str]] = None, cwd: Optional[str] = None,
                 approval_mode: str = "plan", model: Optional[str] = None,
                 extra_args: Optional[Sequence[str]] = None, env: Optional[Dict[str, str]] = None):
        if approval_mode not in {"plan", "default"}:
            raise BackendError("Automated runs only allow approval_mode 'plan' (read-only) or 'default'")
        self.command = list(command or [os.environ.get("DV_GEMINI_BIN", "gemini")])
        self.cwd = cwd
        self.approval_mode = approval_mode
        self.model = model
        self.extra_args = list(extra_args or [])
        self.env = env

    def build_argv(self, agent: str, prompt: str) -> List[str]:
        argv = self.command + ["-p", f"@{agent} {prompt}", "-o", "json", "--approval-mode", self.approval_mode]
        if self.model:
            argv += ["-m", self.model]
        return argv + self.extra_args

    @staticmethod
    def _tokens(stats: Any) -> (int, int, Optional[str]):
        tin = tout = 0
        model_name = None
        models = (stats or {}).get("models", {}) if isinstance(stats, dict) else {}
        for name, m in models.items():
            tok = (m or {}).get("tokens", {})
            tin += int(tok.get("prompt", tok.get("input", 0)) or 0)
            tout += int(tok.get("candidates", tok.get("output", 0)) or 0)
            model_name = model_name or name
        return tin, tout, model_name

    def invoke(self, agent, prompt, task_id, timeout_s):
        argv = self.build_argv(agent, prompt)
        start = time.time()
        try:
            proc = subprocess.run(argv, cwd=self.cwd, env=self.env, capture_output=True, text=True,
                                  timeout=timeout_s, check=False, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired as exc:
            raise TaskTimeout(f"{task_id}: gemini CLI exceeded {timeout_s}s") from exc
        except FileNotFoundError as exc:
            raise BackendError(f"Gemini CLI not found: {self.command[0]}") from exc
        latency = time.time() - start
        if proc.returncode != 0:
            raise BackendError(f"gemini exited {proc.returncode}: {proc.stderr.strip()[:500]}")
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return BackendResult(proc.stdout, estimate_tokens(prompt), estimate_tokens(proc.stdout), latency,
                                 estimated_tokens=True)
        text = payload.get("response", "") if isinstance(payload, dict) else str(payload)
        tin, tout, model_name = self._tokens(payload.get("stats") if isinstance(payload, dict) else None)
        estimated = tin == 0 and tout == 0
        if estimated:
            tin, tout = estimate_tokens(prompt), estimate_tokens(text)
        return BackendResult(text, tin, tout, latency, model=model_name, estimated_tokens=estimated, raw=payload)

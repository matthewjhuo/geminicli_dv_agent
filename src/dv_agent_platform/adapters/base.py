"""Base class for all DV tool/infrastructure adapters.

Enforces:
1. Mock-by-default operation; ``adapter_mode='real'`` raises ``AdapterNotValidated`` unless
   explicitly marked validated for that adapter in ``config.adapters_validated``.
2. Explicit human approval via ``ApprovalGate`` for simulation, waveform batch, source/P4
   workspace preparation, and ticket writes.
3. Safe local CLI subprocess execution (``shell=False``, command-guard classification,
   metacharacter injection blocking, timeout, secret redaction, byte-bounded output).
4. Confidentiality check before outbound data transfer.
5. Structured audit logging of every adapter call.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import signal
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..config import FrameworkConfig
from ..errors import AdapterNotValidated, ConfidentialityError, ForbiddenOperation, ConfigError, UnsupportedOperation
from ..security.approval import ApprovalGate, ApprovalRequest
from ..security.command_guard import classify_command
from ..security.redact import redact
from ..telemetry.logger import StructuredLogger

_SHELL_META_RE = re.compile(r"[;&|`\r\n]|\$\(")
_SHELL_WRAPPERS = {"sh", "bash", "zsh", "csh", "tcsh", "ksh", "dash", "cmd", "cmd.exe", "powershell", "pwsh"}


@dataclass
class SubprocessResult:
    returncode: int
    stdout: str
    stderr: str
    argv: List[str]
    timed_out: bool = False
    truncated: bool = False


SubprocessRunner = Callable[
    [Sequence[str], Optional[Path], int, Optional[Dict[str, str]]],
    SubprocessResult,
]


def _truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    if max_bytes <= 0:
        return "", bool(text)
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return text, False
    marker = "\n...[TRUNCATED]"
    marker_bytes = marker.encode("utf-8")
    if max_bytes <= len(marker_bytes):
        return raw[:max_bytes].decode("utf-8", errors="ignore"), True
    clipped = raw[: max_bytes - len(marker_bytes)].decode("utf-8", errors="ignore")
    return clipped + marker, True


class BaseAdapter:
    name: str = "base"

    def __init__(
        self,
        config: FrameworkConfig,
        approval_gate: Optional[ApprovalGate] = None,
        logger: Optional[StructuredLogger] = None,
        subprocess_runner: Optional[SubprocessRunner] = None,
    ):
        self.config = config
        self.approval_gate = approval_gate or ApprovalGate()
        self.logger = logger or StructuredLogger(config.logs_dir / "adapters.jsonl", component=f"adapter.{self.name}")
        self.subprocess_runner = subprocess_runner

    def _adapter_cfg(self) -> Dict[str, Any]:
        return dict(self.config.adapters.get(self.name) or {})

    @property
    def is_mock(self) -> bool:
        if self.config.adapter_mode == "mock":
            return True
        if not self.config.adapters_validated.get(self.name, False):
            raise AdapterNotValidated(
                f"Adapter '{self.name}' requested in 'real' mode, but adapters_validated['{self.name}'] is not True. "
                "Keep DV_ADAPTER_MODE=mock until validated against the actual Linux environment."
            )
        return False

    def _require_approval(self, category: str, summary: str, details: Dict[str, Any],
                          approval_id: Optional[str] = None):
        req = ApprovalRequest(category=category, summary=summary, details=details)
        return self.approval_gate.require(req, approval_id=approval_id)

    def _require_mock(self) -> None:
        if not self.is_mock:
            raise UnsupportedOperation(
                f"Real {self.name} integration is not implemented. Configure a site adapter; "
                "adapters_validated alone does not implement a service connection."
            )

    def _check_outbound_confidentiality(self, external: bool, target: str) -> None:
        if external and not self.config.allow_external_upload:
            raise ConfidentialityError(
                f"Confidentiality policy blocks outbound payload to '{target}' "
                "(DV_ALLOW_EXTERNAL_UPLOAD is False)."
            )

    def _resolve_project_file(self, rel_or_abs: str) -> Path:
        p = Path(rel_or_abs)
        target = (p if p.is_absolute() else self.config.project_root / p).resolve()
        target.relative_to(self.config.project_root)
        return target

    def _build_env(self, extra_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        env = dict(os.environ)
        env["DV_PROJECT_ROOT"] = str(self.config.project_root)
        if extra_env:
            env.update(extra_env)
        return env

    def _command_argv(self, command, values=None) -> List[str]:
        """Tokenize before expansion so paths/parameters remain single arguments."""
        tokens = shlex.split(command) if isinstance(command, str) else list(command)
        env = self._build_env()
        env.update({k: str(v) for k, v in (values or {}).items()})
        pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
        def expand(match):
            key = match.group(1) or match.group(2)
            if key not in env:
                raise ConfigError(f"Unresolved command variable: {key}")
            return env[key]
        return self._validate_argv([pattern.sub(expand, str(t)) for t in tokens])

    def _command_cwd(self) -> Path:
        raw = str(self._adapter_cfg().get("cwd") or ".")
        return self._resolve_project_file(raw.replace("${DV_PROJECT_ROOT}", str(self.config.project_root)))

    def _validate_argv(self, argv: Sequence[str]) -> List[str]:
        if not argv:
            raise ValueError("Adapter command argv must not be empty")
        cmd_list: List[str] = []
        for tok in argv:
            if not isinstance(tok, str) or not tok.strip():
                raise ValueError(f"Invalid command token: {tok!r}")
            if _SHELL_META_RE.search(tok):
                raise ForbiddenOperation(f"Shell metacharacter/injection detected in command argument: {tok!r}")
            cmd_list.append(tok)

        exe_name = Path(cmd_list[0]).name.lower()
        if exe_name in _SHELL_WRAPPERS and any(a.lower() in {"-c", "/c", "-command"} for a in cmd_list[1:]):
            raise ForbiddenOperation(f"Shell wrapper invocation via '{exe_name}' is forbidden in adapters")

        raw_joined = " ".join(cmd_list)
        quoted_joined = " ".join(shlex.quote(a) for a in cmd_list)
        for candidate in (raw_joined, quoted_joined):
            verdict = classify_command(candidate, allow_network=self.config.allow_external_upload)
            if verdict.category == "forbidden":
                raise ForbiddenOperation(verdict.reason)
        return cmd_list

    def _run_command(
        self,
        argv: Sequence[str],
        cwd: Optional[Path] = None,
        timeout_s: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
        max_output_bytes: Optional[int] = None,
    ) -> SubprocessResult:
        cmd_list = self._validate_argv(argv)
        acfg = self._adapter_cfg()
        eff_timeout = int(timeout_s if timeout_s is not None else acfg.get("timeout_s", self.config.task_timeout_s))
        eff_max_bytes = int(max_output_bytes if max_output_bytes is not None else acfg.get("max_output_bytes", 65536))
        if eff_timeout <= 0 or eff_max_bytes <= 0:
            raise ConfigError("timeout_s and max_output_bytes must be positive")
        run_cwd = Path(cwd).resolve() if cwd is not None else self.config.project_root
        run_cwd.relative_to(self.config.project_root)
        run_env = self._build_env(env)

        if self.subprocess_runner is not None:
            raw_res = self.subprocess_runner(cmd_list, run_cwd, eff_timeout, run_env)
        else:
            # Drain both pipes concurrently, retaining only a bounded prefix.
            # Extra lookahead lets redaction recognize a token crossing the limit.
            cap = eff_max_bytes + 8192
            buffers = [bytearray(), bytearray()]
            totals = [0, 0]
            def drain(pipe, index):
                try:
                    while True:
                        chunk = pipe.read(8192)
                        if not chunk:
                            break
                        totals[index] += len(chunk)
                        remaining = cap - len(buffers[index])
                        if remaining > 0:
                            buffers[index].extend(chunk[:remaining])
                finally:
                    pipe.close()
            proc = subprocess.Popen(
                cmd_list, cwd=str(run_cwd), env=run_env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                start_new_session=(os.name != "nt"),
            )
            threads = [threading.Thread(target=drain, args=(pipe, i), daemon=True)
                       for i, pipe in enumerate((proc.stdout, proc.stderr))]
            for thread in threads:
                thread.start()
            timed_out = False
            try:
                proc.wait(timeout=eff_timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                if os.name != "nt":
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    proc.kill()
                proc.wait()
            for thread in threads:
                thread.join(timeout=1)
            if any(t.is_alive() for t in threads):
                # A descendant can outlive a successfully exited wrapper and hold
                # its pipes. Terminate the Linux process group before returning.
                timed_out = True
                if os.name != "nt":
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    for thread in threads:
                        thread.join(timeout=1)
            raw_res = SubprocessResult(
                124 if timed_out else proc.returncode,
                buffers[0].decode("utf-8", errors="replace"),
                buffers[1].decode("utf-8", errors="replace"),
                cmd_list, timed_out, any(n > eff_max_bytes for n in totals),
            )

        clean_out, trunc_out = _truncate_utf8(redact(raw_res.stdout or ""), eff_max_bytes)
        clean_err, trunc_err = _truncate_utf8(redact(raw_res.stderr or ""), eff_max_bytes)
        res = SubprocessResult(
            returncode=int(raw_res.returncode),
            stdout=clean_out,
            stderr=clean_err,
            argv=list(raw_res.argv or cmd_list),
            timed_out=bool(raw_res.timed_out),
            truncated=bool(getattr(raw_res, "truncated", False) or trunc_out or trunc_err),
        )
        self.logger.log(
            f"{self.name}_subprocess",
            argv=cmd_list,
            returncode=res.returncode,
            timed_out=res.timed_out,
            truncated=res.truncated,
        )
        return res

    def _load_mock_infra(self) -> Dict[str, Any]:
        candidates = []
        if self.config.fixtures_dir:
            candidates.append(self.config.fixtures_dir / "mock_infra" / "infra_data.json")
        candidates.append(self.config.project_root / "fixtures" / "mock_infra" / "infra_data.json")
        for c in candidates:
            if c.is_file():
                return json.loads(c.read_text(encoding="utf-8"))
        return {}

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..config import FrameworkConfig
from ..errors import AdapterNotValidated, ConfidentialityError, ForbiddenOperation
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
        env.setdefault("DV_PROJECT_ROOT", str(self.config.project_root))
        if extra_env:
            env.update(extra_env)
        return env

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
        run_cwd = Path(cwd).resolve() if cwd is not None else self.config.project_root
        run_cwd.relative_to(self.config.project_root)
        run_env = self._build_env(env)

        if self.subprocess_runner is not None:
            raw_res = self.subprocess_runner(cmd_list, run_cwd, eff_timeout, run_env)
        else:
            try:
                proc = subprocess.run(
                    cmd_list,
                    cwd=str(run_cwd),
                    env=run_env,
                    capture_output=True,
                    text=True,
                    timeout=eff_timeout,
                    check=False,
                    shell=False,
                    encoding="utf-8",
                    errors="replace",
                )
                raw_res = SubprocessResult(
                    returncode=proc.returncode,
                    stdout=proc.stdout or "",
                    stderr=proc.stderr or "",
                    argv=cmd_list,
                    timed_out=False,
                )
            except subprocess.TimeoutExpired as exc:
                out_s = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", errors="replace")
                err_s = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", errors="replace")
                raw_res = SubprocessResult(
                    returncode=124,
                    stdout=out_s,
                    stderr=err_s or f"Command timed out after {eff_timeout}s",
                    argv=cmd_list,
                    timed_out=True,
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

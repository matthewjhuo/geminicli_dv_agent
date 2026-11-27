"""Synopsys VCS adapter (read-only compile/sim log parsing + approval-gated compile & simulation)."""

from __future__ import annotations

import re
import shlex
from typing import Any, Dict, List, Optional, Sequence

from .base import BaseAdapter
from .logs import is_failure_line

_ERR_RE = re.compile(r"(UVM_FATAL|UVM_ERROR|Error-\[[^\]]+\]|Assertion .* failed|Offending .*|PH_TIMEOUT)")
_COMPILE_ERR_RE = re.compile(r"Error-\[([^\]]+)\]\s*(.*)")
_COMPILE_WARN_RE = re.compile(r"Warning-\[([^\]]+)\]\s*(.*)")
_TIME_RE = re.compile(r"(?:@|\bat\s+)(\d+\s*(?:fs|ps|ns|us|ms|s))", re.I)


class VCSAdapter(BaseAdapter):
    name = "vcs"

    def parse_log(self, log_path: str, context_lines: int = 5, max_hits: int = 20) -> Dict[str, Any]:
        _ = self.is_mock  # validates mode policy
        path = self._resolve_project_file(log_path)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        hits: List[Dict[str, Any]] = []
        seed: Optional[str] = None
        testname: Optional[str] = None
        error_count = 0
        context_lines = max(0, min(int(context_lines), 100))
        max_hits = max(1, min(int(max_hits), 1000))
        for idx, line in enumerate(lines):
            if testname is None:
                m_t = re.search(r"\+UVM_TESTNAME=([^\s]+)", line)
                if m_t:
                    testname = m_t.group(1)
            if seed is None:
                m_s = re.search(r"\+ntb_random_seed=(\d+)", line)
                if m_s:
                    seed = m_s.group(1)
            if not is_failure_line(line):
                continue
            error_count += 1
            if len(hits) < max_hits:
                m_time = _TIME_RE.search(line)
                lo = max(0, idx - context_lines)
                hi = min(len(lines), idx + context_lines + 1)
                hits.append({
                    "line": idx + 1,
                    "sim_time": m_time.group(1) if m_time else None,
                    "signature": line.strip()[:200],
                    "window": "\n".join(lines[lo:hi]),
                })
        result = {
            "adapter": "vcs",
            "mode": "mock" if self.config.adapter_mode == "mock" else "real",
            "log_path": log_path,
            "test": testname,
            "seed": seed,
            "total_lines": len(lines),
            "first_failure": hits[0] if hits else None,
            "error_count": error_count,
            "hits_truncated": error_count > len(hits),
            "hits": hits,
        }
        self.logger.log("vcs_parse_log", log_path=log_path, error_count=error_count)
        return result

    def parse_compile_log(self, log_path: str, context_lines: int = 3, max_hits: int = 50) -> Dict[str, Any]:
        _ = self.is_mock
        path = self._resolve_project_file(log_path)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        errors: List[Dict[str, Any]] = []
        warnings: List[Dict[str, Any]] = []
        error_count = warning_count = 0
        max_hits = max(1, min(int(max_hits), 1000))
        context_lines = max(0, min(int(context_lines), 100))
        for idx, line in enumerate(lines):
            m_err = _COMPILE_ERR_RE.search(line)
            error_count += bool(m_err)
            if m_err and len(errors) < max_hits:
                lo = max(0, idx - context_lines)
                hi = min(len(lines), idx + context_lines + 1)
                errors.append({
                    "line": idx + 1,
                    "code": m_err.group(1),
                    "message": m_err.group(2).strip()[:200],
                    "window": "\n".join(lines[lo:hi]),
                })
                continue
            m_warn = _COMPILE_WARN_RE.search(line)
            warning_count += bool(m_warn)
            if m_warn and len(warnings) < max_hits:
                warnings.append({
                    "line": idx + 1,
                    "code": m_warn.group(1),
                    "message": m_warn.group(2).strip()[:200],
                })
        result = {
            "adapter": "vcs",
            "mode": "mock" if self.config.adapter_mode == "mock" else "real",
            "log_path": log_path,
            "total_lines": len(lines),
            "error_count": error_count,
            "warning_count": warning_count,
            "hits_truncated": error_count > len(errors) or warning_count > len(warnings),
            "errors": errors,
            "warnings": warnings,
        }
        self.logger.log("vcs_parse_compile_log", log_path=log_path, error_count=len(errors), warning_count=len(warnings))
        return result

    def _build_compile_argv(self, build_target: str, extra_args: Optional[Sequence[str]] = None, test: str = "") -> List[str]:
        acfg = self._adapter_cfg()
        prefix = list(acfg.get("scheduler_prefix") or [])
        command = acfg.get("compile_cmd") or self.config.testbench.get("compile_cmd")
        if command:
            argv = (self._command_argv(prefix) if prefix else []) + self._command_argv(
                command, {"BUILD_TARGET": build_target, "TEST": test})
            return self._validate_argv(argv + list(extra_args or []))
        vcs_bin = str(acfg.get("bin") or "vcs")
        default_flags = [str(f) for f in (acfg.get("default_flags") or [])]
        argv = prefix + [vcs_bin, "-full64"] + default_flags + ["-o", build_target]
        if extra_args:
            argv.extend(str(a) for a in extra_args)
        return self._command_argv(argv)

    def compile(
        self,
        build_target: str = "simv",
        extra_args: Optional[Sequence[str]] = None,
        dry_run: Optional[bool] = None,
        approved: bool = False,
        approval_id: Optional[str] = None,
        test: str = "",
    ) -> Dict[str, Any]:
        is_dry = self.config.dry_run if dry_run is None else bool(dry_run)
        argv = self._build_compile_argv(build_target=build_target, extra_args=extra_args, test=test)
        cmd = shlex.join(argv)
        cwd = self._command_cwd()
        details = {"build_target": build_target, "test": test, "argv": argv, "command": cmd, "cwd": str(cwd)}
        if is_dry:
            self.logger.log("vcs_compile_dry_run", **details)
            return {"status": "dry_run", **details, "requires_approval": True}
        decision = self._require_approval(
            "simulation",
            f"Compile VCS target '{build_target}'",
            details,
            approval_id,
        )
        mock = self.is_mock
        if mock:
            self.logger.log("vcs_compile_executed", mock=True, approver=decision.approver, **details)
            return {
                "status": "compiled_mock",
                "approved_by": decision.approver,
                "build_target": build_target,
                "command": cmd,
                "argv": argv,
                "returncode": 0,
            }
        res = self._run_command(argv, cwd=cwd)
        self.logger.log("vcs_compile_executed", mock=False, approver=decision.approver, returncode=res.returncode)
        out = {
            "status": "compiled" if res.returncode == 0 and not res.timed_out else "failed",
            "approved_by": decision.approver,
            "build_target": build_target,
            "cwd": str(cwd),
            "command": cmd,
            "argv": res.argv,
            "returncode": res.returncode,
            "timed_out": res.timed_out,
            "truncated": res.truncated,
            "stdout": res.stdout,
            "stderr": res.stderr,
        }
        if res.advisory is not None:
            out["advisory"] = res.advisory
        return out

    def _build_sim_argv(self, test: str, seed: int, dump_fsdb: bool) -> List[str]:
        acfg = self._adapter_cfg()
        prefix = list(acfg.get("scheduler_prefix") or [])
        sim_cmd = acfg.get("sim_cmd") or self.config.testbench.get("sim_cmd") or ["./simv"]
        template = str(sim_cmd)
        base = self._command_argv(sim_cmd, {"TEST": test, "SEED": int(seed), "DUMP_FSDB": int(dump_fsdb)})
        argv = (self._command_argv(prefix) if prefix else []) + base
        if not re.search(r"\$\{?TEST\b", template) and not any("+UVM_TESTNAME=" in a for a in base):
            argv.append(f"+UVM_TESTNAME={test}")
        if not re.search(r"\$\{?SEED\b", template) and not any("+ntb_random_seed=" in a for a in base):
            argv.append(f"+ntb_random_seed={int(seed)}")
        if dump_fsdb and not re.search(r"\$\{?DUMP_FSDB\b", template) and not any("DUMP_FSDB=" in a for a in base):
            argv.append("+DUMP_FSDB=1")
        return self._validate_argv(argv)

    def run_simulation(
        self,
        test: str,
        seed: int = 1,
        dump_fsdb: bool = False,
        dry_run: Optional[bool] = None,
        approval_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        is_dry = self.config.dry_run if dry_run is None else bool(dry_run)
        argv = self._build_sim_argv(test, int(seed), bool(dump_fsdb))
        cmd = shlex.join(argv)
        cwd = self._command_cwd()
        details = {"test": test, "seed": int(seed), "dump_fsdb": bool(dump_fsdb), "command": cmd,
                   "argv": argv, "cwd": str(cwd)}
        if is_dry:
            self.logger.log("vcs_sim_dry_run", **details)
            return {"status": "dry_run", **details, "requires_approval": True}
        decision = self._require_approval("simulation", f"Run VCS simulation {test} (seed={seed})", details, approval_id)
        mock = self.is_mock
        if mock:
            self.logger.log("vcs_sim_executed", mock=True, approver=decision.approver, **details)
            return {
                "status": "simulated_mock",
                "approved_by": decision.approver,
                "command": cmd,
                "log_path": None,
                "argv": argv,
            }
        res = self._run_command(argv, cwd=cwd)
        log_template = self._adapter_cfg().get("log_path")
        log_path = None
        if log_template:
            log_tokens = self._command_argv([log_template], {"TEST": test, "SEED": int(seed)})
            target = self._resolve_project_file(log_tokens[0])
            if target.is_file():
                log_path = target.relative_to(self.config.project_root).as_posix()
        self.logger.log("vcs_sim_executed", mock=False, approver=decision.approver, returncode=res.returncode)
        out = {
            "status": "executed" if res.returncode == 0 and not res.timed_out else "failed",
            "approved_by": decision.approver,
            "command": cmd,
            "argv": res.argv,
            "returncode": res.returncode,
            "timed_out": res.timed_out,
            "truncated": res.truncated,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "log_path": log_path,
            "cwd": str(cwd),
        }
        if res.advisory is not None:
            out["advisory"] = res.advisory
        return out

"""ChipMEM-inspired Statistical Tool-Retry & Recovery Strategy Memory.

Implements hierarchical Bayesian ``Beta(1, 1)`` estimation in pure Python stdlib SQLite:

1. **Hierarchical Retry Predictor** (shrinkage ``K = 8.0``):
   ``p0 -> p1(cmd) -> p2(cmd, err_class) -> p3(cmd, err_class, min(retry, 3))``
   Predicts the probability that simply retrying an EDA command will succeed.
   When ``p_retry < threshold`` (default ``0.20``), signals the agent to switch
   away from blind retries and consult the Recovery Strategy Advisor.

2. **Hierarchical Recovery Strategy Advisor** (shrinkage ``K = 8.0``):
   ``q0 -> q1(action) -> q2(action, err_class) -> q3(action, err_class, stage)``
   Ranks historically verified recovery actions for the observed ``(err_class, stage)``.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

DEFAULT_SHRINKAGE_K = 8.0
DEFAULT_SWITCH_THRESHOLD = 0.20

DEFAULT_RECOVERY_CANDIDATES: Dict[str, List[str]] = {
    "timeout": [
        "reduce_sim_window_or_seed_count",
        "inspect_watchdog_and_deadlock_signals",
        "switch_scheduler_queue",
    ],
    "license_unavailable": [
        "wait_and_retry_license",
        "switch_scheduler_queue",
        "verify_lm_license_file_env",
    ],
    "vcs_compile_syntax": [
        "parse_compile_log_first_syntax_error",
        "inspect_sv_context_around_error_line",
        "check_sv_version_and_macro_defines",
    ],
    "vcs_compile_undef": [
        "parse_filelist_and_incdir_order",
        "inspect_sv_package_imports",
        "check_missing_plusargs_or_defines",
    ],
    "vcs_elab_error": [
        "parse_compile_log_elab_mismatch",
        "inspect_sv_port_and_param_bindings",
        "check_timescale_consistency",
    ],
    "uvm_fatal": [
        "parse_first_failure_log",
        "backward_trace_waveform",
        "check_recent_p4_changes",
    ],
    "uvm_error": [
        "parse_first_failure_log",
        "backward_trace_waveform",
        "inspect_scoreboard_expectations",
    ],
    "sva_assertion_fail": [
        "backward_trace_waveform",
        "inspect_sva_property_and_rtl_driver",
        "check_reset_and_clock_gating_preconditions",
    ],
    "fsdb_missing_or_corrupt": [
        "verify_fsdb_path_and_dump_flag",
        "find_signal_in_scope_hierarchy",
        "re_extract_narrower_time_window",
    ],
    "p4_auth_or_client": [
        "check_p4_status_and_client_mapping",
        "verify_p4_ticket_read_only",
        "use_project_relative_file_paths",
    ],
    "missing_file_or_binary": [
        "verify_tool_bin_and_project_paths",
        "parse_filelist_missing_entries",
        "check_company_profile_config",
    ],
    "unknown_failure": [
        "parse_first_failure_log",
        "inspect_stderr_tail",
        "escalate_with_minimal_repro",
    ],
}

_ERR_PATTERNS: Sequence[tuple[str, re.Pattern[str]]] = (
    ("license_unavailable", re.compile(r"flexlm|license\s+(checkout|server|feature|unavailable)|snpslmd", re.I)),
    ("vcs_compile_syntax", re.compile(r"Error-\[SE\]|syntax\s+error", re.I)),
    ("vcs_compile_undef", re.compile(r"Error-\[(?:XMRE|IPDW|UII|TMAFTC)\]|undefined\s+(?:identifier|module|macro)|cannot\s+find\s+include", re.I)),
    ("vcs_elab_error", re.compile(r"Error-\[[A-Z0-9_]+\]|elaboration\s+failed|port\s+connection", re.I)),
    ("sva_assertion_fail", re.compile(r"assertion\s+.*failed|\[SVA\]|AST_[A-Z0-9_]+", re.I)),
    ("uvm_fatal", re.compile(r"\bUVM_FATAL\b|PH_TIMEOUT", re.I)),
    ("uvm_error", re.compile(r"\bUVM_ERROR\b", re.I)),
    ("fsdb_missing_or_corrupt", re.compile(r"fsdb(?:report)?.*(?:not\s+found|corrupt|invalid\s+signal|cannot\s+open)", re.I)),
    ("p4_auth_or_client", re.compile(r"Perforce\s+password|P4CLIENT|not\s+under\s+client|session\s+has\s+expired", re.I)),
    ("missing_file_or_binary", re.compile(r"no\s+such\s+file|cannot\s+open\s+file|command\s+not\s+found|file\s+not\s+found", re.I)),
)


def classify_eda_error(
    tool: str,
    returncode: int,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
) -> str:
    """Deterministic classification of EDA/DV subprocess failures into ChipMEM error classes."""
    if timed_out:
        return "timeout"
    if int(returncode) == 0:
        return "none"
    blob = f"{stderr or ''}\n{stdout or ''}"
    for err_class, pat in _ERR_PATTERNS:
        if pat.search(blob):
            return err_class
    tool_l = (tool or "").lower()
    if "fsdb" in tool_l or "verdi" in tool_l:
        return "fsdb_missing_or_corrupt"
    if "p4" in tool_l or "perforce" in tool_l:
        return "p4_auth_or_client"
    return "unknown_failure"


class StatisticalToolMemory:
    """SQLite-backed ChipMEM Statistical Memory (Beta-binomial hierarchical estimator)."""

    def __init__(
        self,
        db_path: Path | str,
        *,
        shrinkage_k: float = DEFAULT_SHRINKAGE_K,
        switch_threshold: float = DEFAULT_SWITCH_THRESHOLD,
        mode: str = "evolving",
    ):
        if mode not in {"evolving", "frozen"}:
            raise ValueError(f"mode must be 'evolving' or 'frozen', got {mode!r}")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.shrinkage_k = float(shrinkage_k)
        self.switch_threshold = float(switch_threshold)
        self.mode = mode
        self._init_db()

    @contextlib.contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS retry_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cmd TEXT NOT NULL,
                    err_class TEXT NOT NULL,
                    retry_bucket INTEGER NOT NULL,
                    succeeded INTEGER NOT NULL,
                    stage TEXT NOT NULL DEFAULT 'default',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_retry_cmd_err
                    ON retry_events(cmd, err_class, retry_bucket);

                CREATE TABLE IF NOT EXISTS recovery_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    err_class TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    succeeded INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_rec_action_err_stage
                    ON recovery_events(action, err_class, stage);
                """
            )

    @staticmethod
    def _bucket_retry(retry_count: int) -> int:
        return max(0, min(int(retry_count), 3))

    def record_retry_outcome(
        self,
        cmd: str,
        err_class: str,
        retry_count: int,
        succeeded: bool,
        *,
        stage: str = "default",
    ) -> None:
        if self.mode == "frozen":
            return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO retry_events (cmd, err_class, retry_bucket, succeeded, stage, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(cmd).strip() or "unknown",
                    str(err_class).strip() or "unknown_failure",
                    self._bucket_retry(retry_count),
                    1 if succeeded else 0,
                    str(stage).strip() or "default",
                    time.time(),
                ),
            )

    def predict_retry_success(
        self,
        cmd: str,
        err_class: str,
        retry_count: int,
    ) -> Dict[str, Any]:
        """Compute hierarchical Bayesian ``p0 -> p1 -> p2 -> p3`` retry success probability."""
        c = str(cmd).strip() or "unknown"
        e = str(err_class).strip() or "unknown_failure"
        rb = self._bucket_retry(retry_count)
        k = self.shrinkage_k

        with self._connect() as conn:
            r0 = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(succeeded), 0) AS s FROM retry_events").fetchone()
            r1 = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(succeeded), 0) AS s FROM retry_events WHERE cmd = ?",
                (c,),
            ).fetchone()
            r2 = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(succeeded), 0) AS s FROM retry_events WHERE cmd = ? AND err_class = ?",
                (c, e),
            ).fetchone()
            r3 = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(succeeded), 0) AS s FROM retry_events WHERE cmd = ? AND err_class = ? AND retry_bucket = ?",
                (c, e, rb),
            ).fetchone()

        n0, s0 = int(r0["n"]), int(r0["s"])
        n1, s1 = int(r1["n"]), int(r1["s"])
        n2, s2 = int(r2["n"]), int(r2["s"])
        n3, s3 = int(r3["n"]), int(r3["s"])

        p0 = (1.0 + s0) / (2.0 + n0)
        p1 = (s1 + k * p0) / (n1 + k)
        p2 = (s2 + k * p1) / (n2 + k)
        p3 = (s3 + k * p2) / (n3 + k)

        return {
            "cmd": c,
            "err_class": e,
            "retry_bucket": rb,
            "p_retry": round(p3, 4),
            "hierarchy": {
                "p0_global": round(p0, 4),
                "p1_cmd": round(p1, 4),
                "p2_cmd_err": round(p2, 4),
                "p3_cmd_err_retry": round(p3, 4),
            },
            "counts": {"global": n0, "cmd": n1, "cmd_err": n2, "cmd_err_retry": n3},
            "threshold": self.switch_threshold,
            "should_switch_strategy": p3 < self.switch_threshold,
        }

    def record_recovery_outcome(
        self,
        action: str,
        err_class: str,
        succeeded: bool,
        *,
        stage: str = "default",
    ) -> None:
        if self.mode == "frozen":
            return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO recovery_events (action, err_class, stage, succeeded, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(action).strip(),
                    str(err_class).strip() or "unknown_failure",
                    str(stage).strip() or "default",
                    1 if succeeded else 0,
                    time.time(),
                ),
            )

    def recommend_recoveries(
        self,
        err_class: str,
        *,
        stage: str = "default",
        candidate_actions: Optional[Sequence[str]] = None,
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """Rank recovery strategies using hierarchical ``q0 -> q1 -> q2 -> q3`` posteriors."""
        e = str(err_class).strip() or "unknown_failure"
        st = str(stage).strip() or "default"
        k = self.shrinkage_k

        with self._connect() as conn:
            observed = [
                r["action"]
                for r in conn.execute(
                    "SELECT DISTINCT action FROM recovery_events WHERE err_class = ?",
                    (e,),
                ).fetchall()
            ]
            actions: List[str] = []
            for a in list(candidate_actions or []) + observed + DEFAULT_RECOVERY_CANDIDATES.get(
                e, DEFAULT_RECOVERY_CANDIDATES["unknown_failure"]
            ):
                if a and a not in actions:
                    actions.append(a)

            g = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(succeeded), 0) AS s FROM recovery_events").fetchone()
            n0, s0 = int(g["n"]), int(g["s"])
            q0 = (1.0 + s0) / (2.0 + n0)

            ranked: List[Dict[str, Any]] = []
            for idx, action in enumerate(actions):
                r1 = conn.execute(
                    "SELECT COUNT(*) AS n, COALESCE(SUM(succeeded), 0) AS s FROM recovery_events WHERE action = ?",
                    (action,),
                ).fetchone()
                r2 = conn.execute(
                    "SELECT COUNT(*) AS n, COALESCE(SUM(succeeded), 0) AS s FROM recovery_events WHERE action = ? AND err_class = ?",
                    (action, e),
                ).fetchone()
                r3 = conn.execute(
                    "SELECT COUNT(*) AS n, COALESCE(SUM(succeeded), 0) AS s FROM recovery_events WHERE action = ? AND err_class = ? AND stage = ?",
                    (action, e, st),
                ).fetchone()
                n1, s1 = int(r1["n"]), int(r1["s"])
                n2, s2 = int(r2["n"]), int(r2["s"])
                n3, s3 = int(r3["n"]), int(r3["s"])
                q1 = (s1 + k * q0) / (n1 + k)
                q2 = (s2 + k * q1) / (n2 + k)
                q3 = (s3 + k * q2) / (n3 + k)
                ranked.append(
                    {
                        "action": action,
                        "err_class": e,
                        "stage": st,
                        "q_success": round(q3, 4),
                        "samples": n3,
                        "err_samples": n2,
                        "_tie_order": idx,
                    }
                )

        ranked.sort(key=lambda item: (-item["q_success"], -item["err_samples"], item["_tie_order"]))
        return [{k2: v2 for k2, v2 in item.items() if k2 != "_tie_order"} for item in ranked[: max(1, int(top_k))]]

    def advise_on_failure(
        self,
        cmd: str,
        err_class: str,
        retry_count: int,
        *,
        stage: str = "default",
        top_k: int = 3,
    ) -> Dict[str, Any]:
        pred = self.predict_retry_success(cmd, err_class, retry_count)
        recs = self.recommend_recoveries(err_class, stage=stage, top_k=top_k)
        return {
            "cmd": pred["cmd"],
            "err_class": pred["err_class"],
            "retry_count": int(retry_count),
            "p_retry": pred["p_retry"],
            "should_switch_strategy": pred["should_switch_strategy"],
            "threshold": pred["threshold"],
            "recommended_recoveries": recs,
        }

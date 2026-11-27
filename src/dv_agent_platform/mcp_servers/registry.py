"""Factory registering the 6 DV MCP server profiles (dv-vcs, dv-verdi, dv-p4,
dv-regression, dv-ado, dv-jira) backed by mock-first adapters and approval gates.
"""

from __future__ import annotations

from typing import Optional

from ..adapters import ADOAdapter, JIRAAdapter, PerforceAdapter, RegressionAdapter, VCSAdapter, VerdiAdapter
from ..config import FrameworkConfig
from ..dv_tools import parse_filelist, inspect_sv_context
from ..security.approval import ApprovalGate, approver_from_env
from ..telemetry.logger import StructuredLogger
from .protocol import MCPServer, ToolSpec

SERVER_PROFILES = ("dv-vcs", "dv-verdi", "dv-p4", "dv-regression", "dv-ado", "dv-jira")


def create_dv_mcp_server(
    profile: str,
    config: FrameworkConfig,
    approval_gate: Optional[ApprovalGate] = None,
    fence_outputs: bool = True,
) -> MCPServer:
    if profile not in SERVER_PROFILES:
        raise ValueError(f"Unknown MCP server profile {profile!r}. Expected one of {SERVER_PROFILES}")
    logger = StructuredLogger(config.logs_dir / "mcp.jsonl", component=f"mcp.{profile}")
    gate = approval_gate or ApprovalGate(approver=approver_from_env(), logger=logger.child("approval"))
    srv = MCPServer(name=profile, fence_outputs=fence_outputs, logger=logger)

    if profile == "dv-vcs":
        vcs = VCSAdapter(config, approval_gate=gate, logger=logger.child("vcs"))
        srv.register(ToolSpec(
            name="compile",
            description="Preview or approval-gated execution of the configured company build command.",
            input_schema={"type": "object", "properties": {
                "build_target": {"type": "string", "default": "simv"},
                "test": {"type": "string", "default": ""},
                "extra_args": {"type": "array", "items": {"type": "string"}},
                "dry_run": {"type": "boolean"}, "approval_id": {"type": "string"}}},
            handler=lambda a: vcs.compile(build_target=a.get("build_target", "simv"),
                extra_args=a.get("extra_args"), test=a.get("test", ""),
                dry_run=a.get("dry_run"), approval_id=a.get("approval_id")),
            read_only=False,
        ))
        srv.register(ToolSpec(
            name="parse_filelist",
            description="Read-only filelist index (not simulator preprocessing or elaboration).",
            input_schema={"type": "object", "required": ["filelist_path"],
                          "properties": {"filelist_path": {"type": "string"}}},
            handler=lambda a: parse_filelist(a["filelist_path"], config.project_root,
                allowed_roots=config.testbench.get("read_only_roots", [])),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="inspect_sv_context",
            description="Read-only regex-based SV/UVM index; not a complete semantic parser.",
            input_schema={"type": "object", "required": ["files"],
                          "properties": {"files": {"type": "array", "items": {"type": "string"}}}},
            handler=lambda a: inspect_sv_context(a["files"], config.project_root,
                allowed_roots=config.testbench.get("read_only_roots", [])),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="parse_log",
            description="Read-only bounded extraction of first failure and error signatures from a VCS simulation log.",
            input_schema={
                "type": "object",
                "required": ["log_path"],
                "properties": {
                    "log_path": {"type": "string"},
                    "context_lines": {"type": "integer", "default": 5},
                },
            },
            handler=lambda a: vcs.parse_log(a["log_path"], int(a.get("context_lines", 5))),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="parse_compile_log",
            description="Read-only extraction of VCS compile errors and warnings.",
            input_schema={
                "type": "object",
                "required": ["log_path"],
                "properties": {"log_path": {"type": "string"}},
            },
            handler=lambda a: vcs.parse_compile_log(a["log_path"]),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="run_simulation",
            description="Approval-gated VCS single-seed simulation launch (dry-run by default unless approved).",
            input_schema={
                "type": "object",
                "required": ["test"],
                "properties": {
                    "test": {"type": "string"},
                    "seed": {"type": "integer", "default": 1},
                    "dump_fsdb": {"type": "boolean", "default": False},
                    "dry_run": {"type": "boolean"},
                    "approval_id": {"type": "string"},
                },
            },
            handler=lambda a: vcs.run_simulation(
                a["test"], a.get("seed", 1), a.get("dump_fsdb", False), a.get("dry_run"), a.get("approval_id")
            ),
            read_only=False,
        ))

    elif profile == "dv-verdi":
        verdi = VerdiAdapter(config, approval_gate=gate, logger=logger.child("verdi"))
        srv.register(ToolSpec(
            name="suggest_window",
            description="Read-only Verdi waveform signal & time-window query recommendation.",
            input_schema={
                "type": "object",
                "required": ["hierarchy", "center_time_ns"],
                "properties": {"hierarchy": {"type": "string"}, "center_time_ns": {"type": "integer"}},
            },
            handler=lambda a: verdi.suggest_window(a["hierarchy"], int(a["center_time_ns"])),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="extract_fsdb_slice",
            description="Approval-gated Verdi batch FSDB waveform slice extraction.",
            input_schema={
                "type": "object",
                "required": ["fsdb_path", "signals", "start_ns", "end_ns"],
                "properties": {
                    "fsdb_path": {"type": "string"},
                    "signals": {"type": "array", "items": {"type": "string"}},
                    "start_ns": {"type": "integer"},
                    "end_ns": {"type": "integer"},
                    "dry_run": {"type": "boolean"},
                    "approval_id": {"type": "string"},
                },
            },
            handler=lambda a: verdi.extract_fsdb_slice(
                a["fsdb_path"], a["signals"], int(a["start_ns"]), int(a["end_ns"]), a.get("dry_run"), a.get("approval_id")
            ),
            read_only=False,
        ))
        srv.register(ToolSpec(
            name="index_vcd_to_sqlite",
            description="Normalize a project-scoped VCD waveform into a pruned BTTF SQLite waves.db (clock + zero-toggle pruning).",
            input_schema={
                "type": "object",
                "required": ["vcd_path"],
                "properties": {
                    "vcd_path": {"type": "string"},
                    "db_path": {"type": "string"},
                    "prune_clocks": {"type": "boolean", "default": True},
                    "prune_zero_toggle": {"type": "boolean", "default": True},
                },
            },
            handler=lambda a: verdi.index_vcd_to_sqlite(
                a["vcd_path"],
                a.get("db_path"),
                prune_clocks=a.get("prune_clocks", True),
                prune_zero_toggle=a.get("prune_zero_toggle", True),
            ),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="find_signal_in_scope",
            description="Read-only waveform metadata search by pattern and optional hierarchy scope in waves.db.",
            input_schema={
                "type": "object",
                "required": ["db_path"],
                "properties": {
                    "db_path": {"type": "string"},
                    "pattern": {"type": "string", "default": "*"},
                    "scope": {"type": "string"},
                    "include_pruned": {"type": "boolean", "default": True},
                    "limit": {"type": "integer", "default": 50},
                },
            },
            handler=lambda a: verdi.find_signal_in_scope(
                a["db_path"],
                pattern=a.get("pattern", "*"),
                scope=a.get("scope"),
                include_pruned=a.get("include_pruned", True),
                limit=int(a.get("limit", 50)),
            ),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="get_value_at_time",
            description="Read-only exact signal value query at or immediately preceding a simulation timestamp in waves.db.",
            input_schema={
                "type": "object",
                "required": ["db_path", "signal_name", "time"],
                "properties": {
                    "db_path": {"type": "string"},
                    "signal_name": {"type": "string"},
                    "time": {"type": "integer"},
                    "scope": {"type": "string"},
                },
            },
            handler=lambda a: verdi.get_value_at_time(
                a["db_path"], a["signal_name"], int(a["time"]), scope=a.get("scope")
            ),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="get_first_time_of_value",
            description="Read-only earliest timestamp query when a signal matched a target value in waves.db.",
            input_schema={
                "type": "object",
                "required": ["db_path", "signal_name", "value"],
                "properties": {
                    "db_path": {"type": "string"},
                    "signal_name": {"type": "string"},
                    "value": {"type": "string"},
                    "start_time": {"type": "integer"},
                    "end_time": {"type": "integer"},
                    "scope": {"type": "string"},
                },
            },
            handler=lambda a: verdi.get_first_time_of_value(
                a["db_path"],
                a["signal_name"],
                a["value"],
                start_time=a.get("start_time"),
                end_time=a.get("end_time"),
                scope=a.get("scope"),
            ),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="get_signal_toggles",
            description="Read-only bounded transition history query for a signal in waves.db.",
            input_schema={
                "type": "object",
                "required": ["db_path", "signal_name"],
                "properties": {
                    "db_path": {"type": "string"},
                    "signal_name": {"type": "string"},
                    "start_time": {"type": "integer"},
                    "end_time": {"type": "integer"},
                    "scope": {"type": "string"},
                    "limit": {"type": "integer", "default": 200},
                },
            },
            handler=lambda a: verdi.get_signal_toggles(
                a["db_path"],
                a["signal_name"],
                start_time=a.get("start_time"),
                end_time=a.get("end_time"),
                scope=a.get("scope"),
                limit=int(a.get("limit", 200)),
            ),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="get_scope_toggle_coverage",
            description="Read-only per-scope active vs zero-toggle coverage breakdown from waves.db.",
            input_schema={
                "type": "object",
                "required": ["db_path"],
                "properties": {"db_path": {"type": "string"}, "scope": {"type": "string"}},
            },
            handler=lambda a: verdi.get_scope_toggle_coverage(a["db_path"], scope=a.get("scope")),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="backward_trace_window",
            description="Read-only causal backward trace (t <= violation_time) over specified signals in waves.db.",
            input_schema={
                "type": "object",
                "required": ["db_path", "violation_time", "signals"],
                "properties": {
                    "db_path": {"type": "string"},
                    "violation_time": {"type": "integer"},
                    "signals": {"type": "array", "items": {"type": "string"}},
                    "window_before": {"type": "integer", "default": 50},
                    "scope": {"type": "string"},
                    "limit_per_signal": {"type": "integer", "default": 50},
                },
            },
            handler=lambda a: verdi.backward_trace_window(
                a["db_path"],
                int(a["violation_time"]),
                a["signals"],
                window_before=int(a.get("window_before", 50)),
                scope=a.get("scope"),
                limit_per_signal=int(a.get("limit_per_signal", 50)),
            ),
            read_only=True,
        ))

    elif profile == "dv-p4":
        p4 = PerforceAdapter(config, approval_gate=gate, logger=logger.child("p4"))
        srv.register(ToolSpec(
            name="status",
            description="Read-only Perforce workspace opened-file status.",
            input_schema={"type": "object", "properties": {}},
            handler=lambda _: p4.status(),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="changes",
            description="Read-only recent Perforce changelists.",
            input_schema={
                "type": "object",
                "properties": {"path_pattern": {"type": "string", "default": "//..."}, "max_count": {"type": "integer", "default": 10}},
            },
            handler=lambda a: p4.changes(a.get("path_pattern", "//..."), int(a.get("max_count", 10))),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="describe",
            description="Read-only Perforce changelist summary.",
            input_schema={"type": "object", "required": ["changelist"], "properties": {"changelist": {"type": "integer"}}},
            handler=lambda a: p4.describe(int(a["changelist"])),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="diff",
            description="Read-only Perforce workspace diff.",
            input_schema={"type": "object", "properties": {"files": {"type": "array", "items": {"type": "string"}}}},
            handler=lambda a: p4.diff(a.get("files") or []),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="filelog",
            description="Read-only Perforce file revision history.",
            input_schema={
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string"}, "limit": {"type": "integer", "default": 10}},
            },
            handler=lambda a: p4.filelog(a["path"], int(a.get("limit", 10))),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="annotate",
            description="Read-only Perforce line-by-line blame/annotate.",
            input_schema={"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}},
            handler=lambda a: p4.annotate(a["path"]),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="prepare_edit",
            description="Approval-gated Perforce changelist preparation (NEVER submits; human submits manually).",
            input_schema={
                "type": "object",
                "required": ["files", "description"],
                "properties": {
                    "files": {"type": "array", "items": {"type": "string"}},
                    "description": {"type": "string"},
                    "dry_run": {"type": "boolean"},
                    "approval_id": {"type": "string"},
                },
            },
            handler=lambda a: p4.prepare_edit(a["files"], a["description"], a.get("dry_run"), a.get("approval_id")),
            read_only=False,
        ))

    elif profile == "dv-regression":
        regr = RegressionAdapter(config, approval_gate=gate, logger=logger.child("regression"))
        srv.register(ToolSpec(
            name="scan_sim_out",
            description="Read-only scan and cluster local simulation logs.",
            input_schema={"type": "object", "properties": {
                "sim_out_dir": {"type": "string", "default": "sim_out"},
                "pattern": {"type": "string", "default": "simv*.log"}}},
            handler=lambda a: regr.scan_sim_out(a.get("sim_out_dir", "sim_out"), a.get("pattern", "simv*.log")),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="cluster_failures",
            description="Read-only normalized failure signature clustering across regression seeds.",
            input_schema={"type": "object", "properties": {"run_id": {"type": "string", "default": "latest"}}},
            handler=lambda a: regr.cluster_failures(a.get("run_id", "latest")),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="coverage_summary",
            description="Read-only URG coverage dashboard parser.",
            input_schema={"type": "object", "properties": {"report_path": {"type": "string"}}},
            handler=lambda a: regr.coverage_summary(a.get("report_path", "fixtures/coverage/urg_dashboard.txt")),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="launch",
            description="Approval-gated regression suite launch.",
            input_schema={
                "type": "object",
                "required": ["suite"],
                "properties": {
                    "suite": {"type": "string"},
                    "seeds": {"type": "integer", "default": 10},
                    "dry_run": {"type": "boolean"},
                    "approval_id": {"type": "string"},
                },
            },
            handler=lambda a: regr.launch(a["suite"], int(a.get("seeds", 10)), a.get("dry_run"), a.get("approval_id")),
            read_only=False,
        ))

    elif profile == "dv-ado":
        ado = ADOAdapter(config, approval_gate=gate, logger=logger.child("ado"))
        srv.register(ToolSpec(
            name="get_work_item",
            description="Read-only Azure DevOps requirement/work-item lookup.",
            input_schema={"type": "object", "required": ["item_id"], "properties": {"item_id": {"type": "string"}}},
            handler=lambda a: ado.get_work_item(a["item_id"]),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="search_requirements",
            description="Read-only Azure DevOps requirement search.",
            input_schema={"type": "object", "properties": {"query": {"type": "string", "default": ""}}},
            handler=lambda a: ado.search_requirements(a.get("query", "")),
            read_only=True,
        ))

    elif profile == "dv-jira":
        jira = JIRAAdapter(config, approval_gate=gate, logger=logger.child("jira"))
        srv.register(ToolSpec(
            name="get_issue",
            description="Read-only JIRA defect/issue lookup.",
            input_schema={"type": "object", "required": ["key"], "properties": {"key": {"type": "string"}}},
            handler=lambda a: jira.get_issue(a["key"]),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="search_issues",
            description="Read-only JIRA issue search.",
            input_schema={"type": "object", "properties": {"query": {"type": "string", "default": ""}}},
            handler=lambda a: jira.search_issues(a.get("query", "")),
            read_only=True,
        ))
        srv.register(ToolSpec(
            name="file_defect",
            description="Approval- and confidentiality-gated JIRA defect creation.",
            input_schema={
                "type": "object",
                "required": ["project_key", "summary", "description"],
                "properties": {
                    "project_key": {"type": "string"},
                    "summary": {"type": "string"},
                    "description": {"type": "string"},
                    "dry_run": {"type": "boolean"},
                    "approval_id": {"type": "string"},
                },
            },
            handler=lambda a: jira.file_defect(
                a["project_key"], a["summary"], a["description"], True, a.get("dry_run"), a.get("approval_id")
            ),
            read_only=False,
        ))

    return srv

"""Minimal, dependency-free JSON-RPC 2.0 MCP stdio server template.

Supports both newline-delimited JSON-RPC messages (MCP 2024-11-05 / 2025-03-26 stdio
transport) and wraps untrusted string/JSON results inside ``<untrusted_dv_data>`` fences
when ``fence_outputs=True`` so downstream models treat adapter payloads as passive data.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, IO, List, Optional

from ..errors import ApprovalRequired, ConfidentialityError, DVAgentError, ForbiddenOperation
from ..security.redact import redact_obj
from ..security.untrusted import wrap_untrusted

PROTOCOL_VERSION = "2024-11-05"


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: Callable[[Dict[str, Any]], Any]
    read_only: bool = True


class MCPServer:
    def __init__(self, name: str, version: str = "0.1.0", fence_outputs: bool = True, logger=None):
        if "_" in name:
            raise ValueError(f"MCP server name {name!r} must use hyphens rather than underscores for Gemini CLI compatibility")
        self.name = name
        self.version = version
        self.fence_outputs = fence_outputs
        self.logger = logger
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def list_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.input_schema,
                "annotations": {"readOnlyHint": t.read_only},
            }
            for t in self._tools.values()
        ]

    def handle_message(self, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        # Notifications have no 'id' and expect no response
        if msg_id is None:
            return None

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": self.name, "version": self.version},
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": self.list_tools()}}
        if method == "tools/call":
            tname = params.get("name", "")
            targs = params.get("arguments") or {}
            spec = self._tools.get(tname)
            if spec is None:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tname}"},
                }
            try:
                raw_out = spec.handler(targs)
                clean_out = redact_obj(raw_out)
                serialized = json.dumps(clean_out, ensure_ascii=False, indent=2)
                text = wrap_untrusted(serialized, f"mcp:{self.name}/{tname}") if self.fence_outputs else serialized
                if self.logger:
                    self.logger.log("mcp_tool_call", server=self.name, tool=tname, status="ok")
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"content": [{"type": "text", "text": text}], "isError": False},
                }
            except (ForbiddenOperation, ApprovalRequired, ConfidentialityError, DVAgentError) as exc:
                if self.logger:
                    self.logger.log("mcp_tool_blocked", level="warning", server=self.name, tool=tname, error=str(exc))
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": f"[{type(exc).__name__}] {exc}"}],
                        "isError": True,
                    },
                }
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    def serve_stdio(self, instream: IO[str] = sys.stdin, outstream: IO[str] = sys.stdout) -> None:
        for line in instream:
            raw = line.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError as exc:
                resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}}
                outstream.write(json.dumps(resp) + "\n")
                outstream.flush()
                continue
            resp = self.handle_message(msg)
            if resp is not None:
                outstream.write(json.dumps(resp, ensure_ascii=False) + "\n")
                outstream.flush()

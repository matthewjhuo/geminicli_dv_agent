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
from ..security.redact import redact, redact_obj
from ..security.untrusted import wrap_untrusted

PROTOCOL_VERSION = "2024-11-05"


def validate_arguments(value: Any, schema: Dict[str, Any], path: str = "arguments") -> None:
    """Validate the basic JSON Schema subset used by registered tool inputs."""
    kind = schema.get("type")
    valid = {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "integer": type(value) is int,
        "number": type(value) in (int, float), "boolean": type(value) is bool,
    }
    if kind in valid and not valid[kind]:
        raise ValueError(f"{path} must be {kind}")
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                raise ValueError(f"{path}.{name} is required")
        for name, item in value.items():
            if name in schema.get("properties", {}):
                validate_arguments(item, schema["properties"][name], f"{path}.{name}")
    if isinstance(value, list):
        for index, item in enumerate(value):
            validate_arguments(item, schema.get("items", {}), f"{path}[{index}]")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} has an unsupported value")


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
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
        method = msg.get("method")
        msg_id = msg.get("id")
        if msg_id is not None and type(msg_id) not in (str, int):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request id"}}
        params = msg.get("params", {})
        if not isinstance(params, dict):
            return ({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32602, "message": "params must be an object"}}
                    if "id" in msg else None)

        # Notifications have no 'id' and expect no response
        if "id" not in msg:
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
            targs = params.get("arguments", {})
            if not isinstance(tname, str):
                return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32602, "message": "Tool name must be a string"}}
            spec = self._tools.get(tname)
            if spec is None:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tname}"},
                }
            try:
                validate_arguments(targs, spec.input_schema)
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
            except Exception as exc:
                # Errors belong to this call; one missing file or bad argument must
                # not tear down the long-lived stdio server.
                detail = redact(f"[{type(exc).__name__}] {exc}")[:2000]
                if self.logger:
                    self.logger.log("mcp_tool_failed", level="warning", server=self.name, tool=tname, error=detail)
                text = wrap_untrusted(detail, f"mcp:{self.name}/{tname}:error") if self.fence_outputs else detail
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": text}],
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

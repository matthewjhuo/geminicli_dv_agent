"""Stdio Model Context Protocol (MCP) server template & DV server registry."""

from .protocol import MCPServer, ToolSpec  # noqa: F401
from .registry import create_dv_mcp_server, SERVER_PROFILES  # noqa: F401

"""CLI entrypoint to run any DV MCP stdio server profile."""

from __future__ import annotations

import argparse
import sys

from ..config import FrameworkConfig
from .registry import SERVER_PROFILES, create_dv_mcp_server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="dv-mcp-server")
    parser.add_argument("profile", choices=SERVER_PROFILES, help="MCP server profile name")
    parser.add_argument("--project-root", default=None)
    args = parser.parse_args(argv)
    cfg = FrameworkConfig.load(project_root=args.project_root)
    server = create_dv_mcp_server(args.profile, cfg)
    server.serve_stdio()
    return 0


if __name__ == "__main__":
    sys.exit(main())

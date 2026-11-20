"""Builds the MCP server and runs it from the ``wasabi-mcp`` command."""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Sequence

from mcp.server.mcpserver import MCPServer

from wasabi_mcp import __version__
from wasabi_mcp.clients import Clients
from wasabi_mcp.config import Settings
from wasabi_mcp.tools import ToolContext, discover_groups

SERVER_NAME = "wasabi-mcp"

INSTRUCTIONS = """\
Tools for Wasabi cloud storage: S3 buckets and objects, IAM, STS, usage stats and \
WACM Connect account governance.

The server is read-only by default: tools that write (W) or destroy (D) refuse until \
the operator sets WASABI_MCP_READ_ONLY=false. Destructive tools also need confirm=true \
on each call. Call server_info to see the current mode, region and configured services."""


def build_server(settings: Settings | None = None) -> MCPServer:
    """An ``MCPServer`` with every discovered tool group registered."""
    resolved = settings if settings is not None else Settings.from_env()
    ctx = ToolContext(settings=resolved, clients=Clients(resolved))
    server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS, version=__version__)
    for name in discover_groups():
        importlib.import_module(f"wasabi_mcp.tools.{name}").register(server, ctx)
    return server


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog=SERVER_NAME, description=INSTRUCTIONS.splitlines()[0])
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="streamable-http only")
    parser.add_argument("--port", type=int, default=8000, help="streamable-http only")
    args = parser.parse_args(argv)
    server = build_server()
    if args.transport == "streamable-http":
        server.run("streamable-http", host=args.host, port=args.port)
    else:
        server.run("stdio")

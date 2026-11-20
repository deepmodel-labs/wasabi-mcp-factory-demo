"""Server information. The reference example for a tool group module."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from wasabi_mcp import __version__
from wasabi_mcp.tools import READ, ToolContext, discover_groups


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings

    @server.tool(name="server_info", annotations=READ)
    def server_info() -> dict[str, Any]:
        """Show this server's version, default region, read-only flag, endpoints, whether
        Wasabi keys and WACM Connect are configured, and the loaded tool groups. Never
        returns credentials."""
        return {
            "version": __version__,
            "region": settings.region,
            "read_only": settings.read_only,
            "endpoints": {
                "s3": settings.s3_endpoint_for(),
                "iam": settings.iam_url,
                "sts": settings.sts_url,
                "stats": settings.stats_url,
                "wacm": settings.wacm_url,
            },
            "keys_configured": settings.has_keys,
            "wacm_configured": settings.has_wacm,
            "groups": discover_groups(),
        }

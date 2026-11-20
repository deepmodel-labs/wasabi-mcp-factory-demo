"""Guards that W and D tools run before any network call (PRD section 5.2)."""

from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError

from wasabi_mcp.config import Settings


class ToolRefused(ToolError):
    """A refused or failed tool call. Its message is what the client sees.

    It subclasses the SDK's ``ToolError`` so the message reaches the client as an
    ``is_error`` result instead of being replaced by a generic crash message.
    """


def require_writable(settings: Settings, action: str) -> None:
    """Refuse ``action`` while the server is read-only."""
    if settings.read_only:
        raise ToolRefused(
            f"{action} refused: the server is read-only. "
            "Set WASABI_MCP_READ_ONLY=false to allow writes."
        )


def require_confirmed(settings: Settings, action: str, confirm: bool) -> None:
    """Refuse destructive ``action`` unless writes are allowed and ``confirm`` is true."""
    require_writable(settings, action)
    if confirm is not True:
        raise ToolRefused(
            f"{action} refused: this is destructive. Call again with confirm=true to proceed."
        )

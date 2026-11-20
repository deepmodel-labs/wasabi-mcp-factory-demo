"""Call tools the way an MCP client would."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as SDKToolError
from mcp.types import TextContent


class ToolError(Exception):
    """A tool call that came back as an error. Its message is the text the client saw."""


def _text(content: list[Any]) -> str:
    return "\n".join(item.text for item in content if isinstance(item, TextContent))


async def call(server: MCPServer, name: str, **arguments: Any) -> Any:
    """Call tool ``name`` and return its structured result.

    A ``{"result": ...}`` wrapper (used for non-dict return types) is unwrapped. Raises
    ``ToolError`` with the error text when the call fails.
    """
    try:
        result = await server.call_tool(name, arguments)
    except SDKToolError as exc:
        raise ToolError(str(exc)) from exc
    if result.is_error:
        raise ToolError(_text(result.content))
    structured = result.structured_content
    if structured is None:
        text = _text(result.content)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    if set(structured) == {"result"}:
        return structured["result"]
    return structured

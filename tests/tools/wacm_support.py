"""Shared bits for the WACM Connect tool tests."""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as SDKToolError

from tests.conftest import FAKE, FAKE_WACM_API_KEY, FAKE_WACM_USERNAME, SECRETS
from tests.helpers import ToolError, _text
from wasabi_mcp.config import Settings
from wasabi_mcp.server import build_server

WACM = FAKE["wacm"]
BASIC = "Basic " + base64.b64encode(f"{FAKE_WACM_USERNAME}:{FAKE_WACM_API_KEY}".encode()).decode()


def read_only_rest_server(rest_settings: Settings) -> MCPServer:
    """A read-only server with WACM configured on the ``FAKE`` host."""
    return build_server(replace(rest_settings, read_only=True))


async def invoke(server: MCPServer, tool: str, **arguments: Any) -> Any:
    """``tests.helpers.call``, usable with tools that take a ``name`` argument.

    ``helpers.call`` names its own positional parameter ``name``, so it can't pass one on.
    """
    try:
        result = await server.call_tool(tool, arguments)
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


def body(request: httpx.Request) -> Any:
    return json.loads(request.content)


def assert_basic_auth(request: httpx.Request) -> None:
    assert request.headers["Authorization"] == BASIC


def assert_no_secrets(value: Any) -> None:
    text = json.dumps(value)
    for secret in SECRETS:
        assert secret not in text


async def assert_refused(server: MCPServer, tool: str, match: str, **arguments: Any) -> None:
    with pytest.raises(ToolError, match=match):
        await invoke(server, tool, **arguments)

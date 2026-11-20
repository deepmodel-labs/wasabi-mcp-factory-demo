"""The tool boundary in ``build_server``: unexpected exceptions never reach the client."""

from __future__ import annotations

import logging
import sys
import types

import httpx
import pytest
import respx
from mcp.client.client import Client
from mcp.server.mcpserver import MCPServer
from mcp.types import TextContent

from tests.conftest import FAKE_ACCESS_KEY, SECRETS
from tests.helpers import ToolError, call
from wasabi_mcp import server as server_module
from wasabi_mcp.config import Settings
from wasabi_mcp.server import build_server
from wasabi_mcp.tools import READ, ToolContext, discover_groups

SESSION_TOKEN = "FwoGZXIvYXdzEFAKESESSIONTOKEN0000"
LEAKY = (
    f"'NoneType' object has no attribute 'get' (key {FAKE_ACCESS_KEY}, secret "
    f"{SECRETS[0]}, wacm {SECRETS[1]}, SessionToken={SESSION_TOKEN})"
)
NEVER_LOGGED = (*SECRETS, FAKE_ACCESS_KEY, SESSION_TOKEN)
CLIENT_MESSAGE = "failed: unexpected internal error (details are in the server log)"


def _register(server: MCPServer, ctx: ToolContext) -> None:
    @server.tool(name="boom_sync", annotations=READ)
    def boom_sync() -> dict[str, str]:
        """Always crashes."""
        raise AttributeError(LEAKY)

    @server.tool(name="boom_async", annotations=READ)
    async def boom_async() -> dict[str, str]:
        """Always crashes, asynchronously."""
        raise AttributeError(LEAKY)

    @server.tool(name="fine_async", annotations=READ)
    async def fine_async(value: int) -> dict[str, int]:
        """Returns its argument."""
        return {"value": value}


@pytest.fixture
def boom_server(rest_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> MCPServer:
    """A ``build_server`` server with a throwaway group of crashing tools."""
    module = types.ModuleType("wasabi_mcp.tools._boom")
    module.register = _register  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "wasabi_mcp.tools._boom", module)
    monkeypatch.setattr(server_module, "discover_groups", lambda: [*discover_groups(), "_boom"])
    return build_server(rest_settings)


async def _client_text(server: MCPServer, name: str, **arguments: object) -> str:
    """The error text a real MCP client sees (through the SDK's request handler)."""
    async with Client(server) as client:
        result = await client.call_tool(name, arguments)
    assert result.is_error
    return "\n".join(c.text for c in result.content if isinstance(c, TextContent))


@pytest.mark.parametrize("name", ["boom_sync", "boom_async"])
async def test_unexpected_exception_is_hidden_from_the_client(boom_server, caplog, name):
    caplog.set_level(logging.DEBUG)
    text = await _client_text(boom_server, name)
    assert f"{name} {CLIENT_MESSAGE}" in text
    for leaked in ("AttributeError", "NoneType", *NEVER_LOGGED):
        assert leaked not in text


@pytest.mark.parametrize("name", ["boom_sync", "boom_async"])
async def test_unexpected_exception_is_logged_redacted(boom_server, caplog, name):
    caplog.set_level(logging.DEBUG)
    await _client_text(boom_server, name)
    ours = [r for r in caplog.records if r.name == "wasabi_mcp"]
    assert len(ours) == 1
    assert ours[0].levelno == logging.ERROR
    logged = ours[0].getMessage()
    assert name in logged
    assert "Traceback" in logged
    assert "AttributeError: 'NoneType' object has no attribute 'get'" in logged
    everything = caplog.text + "".join(r.getMessage() for r in caplog.records)
    for secret in NEVER_LOGGED:
        assert secret not in everything
    # Nothing (ours or the SDK's) logs the raw exception through exc_info.
    assert all(r.exc_info is None for r in caplog.records)


async def test_guarded_async_tool_still_returns(boom_server):
    assert await call(boom_server, "fine_async", value=3) == {"value": 3}


async def test_confirm_refusal_reaches_the_client_unchanged(rest_server):
    text = await _client_text(rest_server, "bucket_delete", bucket="b")
    assert text == (
        "Error executing tool bucket_delete: DeleteBucket refused: this is destructive. "
        "Call again with confirm=true to proceed."
    )


async def test_validation_error_reaches_the_client_unchanged(boom_server):
    with pytest.raises(ToolError, match="Error executing tool fine_async: 1 validation error"):
        await call(boom_server, "fine_async", value="not a number")


async def test_client_error_reaches_the_client_unchanged(server):
    with pytest.raises(ToolError) as caught:
        await call(server, "bucket_policy_get", bucket="no-such-bucket")
    assert str(caught.value).startswith(
        "Error executing tool bucket_policy_get: GetBucketPolicy failed: NoSuchBucket: "
    )


@respx.mock
async def test_transport_error_reaches_the_client_unchanged(rest_server):
    respx.route(host="stats.wasabi.test").mock(
        side_effect=httpx.ConnectError("[Errno 111] Connection refused")
    )
    text = await _client_text(rest_server, "stats_usage_summary")
    assert text.startswith("Error executing tool stats_usage_summary: ")
    assert "failed: couldn't connect to stats.wasabi.test (connection refused)" in text
    assert "unexpected internal error" not in text

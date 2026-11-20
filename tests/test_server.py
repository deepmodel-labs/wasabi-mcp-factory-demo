import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from tests.conftest import FAKE, SECRETS
from tests.helpers import ToolError, call
from wasabi_mcp import __version__
from wasabi_mcp.server import build_server
from wasabi_mcp.tools import DESTRUCTIVE, READ, WRITE, clean, discover_groups


async def test_server_info_reports_configuration(rest_server):
    info = await call(rest_server, "server_info")
    assert info == {
        "version": __version__,
        "region": "us-east-1",
        "read_only": False,
        "endpoints": {
            "s3": FAKE["s3"],
            "iam": FAKE["iam"],
            "sts": FAKE["sts"],
            "stats": FAKE["stats"],
            "wacm": FAKE["wacm"],
        },
        "keys_configured": True,
        "wacm_configured": True,
        "secrets_dir_enabled": False,
        "groups": discover_groups(),
    }


async def test_server_info_on_read_only_server(read_only_server):
    info = await call(read_only_server, "server_info")
    assert info["read_only"] is True
    assert info["keys_configured"] is True
    assert info["wacm_configured"] is False


async def test_server_info_never_returns_secrets(rest_server):
    result = await rest_server.call_tool("server_info", {})
    text = json.dumps(result.structured_content) + "".join(c.text for c in result.content)
    for secret in SECRETS:
        assert secret not in text


async def test_unknown_tool_raises_tool_error(rest_server):
    with pytest.raises(ToolError, match="no_such_tool"):
        await call(rest_server, "no_such_tool")


async def test_every_listed_tool_declares_annotations(rest_server):
    tools = await rest_server.list_tools()
    assert tools
    for tool in tools:
        assert tool.annotations in (READ, WRITE, DESTRUCTIVE), tool.name
        assert tool.description, tool.name


async def test_server_info_is_a_read_tool(rest_server):
    tools = {tool.name: tool for tool in await rest_server.list_tools()}
    assert tools["server_info"].annotations == READ


def test_annotation_kinds():
    assert (READ.read_only_hint, READ.destructive_hint) == (True, False)
    assert (WRITE.read_only_hint, WRITE.destructive_hint) == (False, False)
    assert (DESTRUCTIVE.read_only_hint, DESTRUCTIVE.destructive_hint) == (False, True)
    assert READ.open_world_hint and WRITE.open_world_hint and DESTRUCTIVE.open_world_hint


def test_discover_groups_puts_meta_first_then_alphabetical():
    groups = discover_groups()
    assert groups[0] == "meta"
    assert groups[1:] == sorted(groups[1:])
    package = Path(__file__).parents[1] / "src" / "wasabi_mcp" / "tools"
    modules = {p.stem for p in package.glob("*.py") if not p.stem.startswith("_")}
    assert set(groups) == modules


def test_instructions_describe_the_read_only_default(rest_settings):
    server = build_server(rest_settings)
    assert "WASABI_MCP_READ_ONLY=false" in server.instructions
    assert "confirm=true" in server.instructions


def test_clean_makes_boto3_responses_json_ready():
    response = {
        "ResponseMetadata": {"RequestId": "x"},
        "LastModified": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        "Items": ({"Body": b"text"}, {"Body": b"\xff\xfe"}),
    }
    assert clean(response) == {
        "LastModified": "2026-01-02T03:04:05+00:00",
        "Items": [{"Body": "text"}, {"Body": "//4="}],
    }


def _command() -> str:
    found = shutil.which("wasabi-mcp", path=str(Path(sys.executable).parent))
    assert found, "the wasabi-mcp console script isn't installed"
    return found


async def test_wasabi_mcp_starts_over_stdio_and_lists_server_info():
    params = StdioServerParameters(
        command=_command(),
        args=["--transport", "stdio"],
        env={"PATH": os.environ.get("PATH", ""), "WASABI_MCP_READ_ONLY": "true"},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = (await session.list_tools()).tools
        assert "server_info" in [tool.name for tool in tools]
        result = await session.call_tool("server_info", {})
        assert result.structured_content["read_only"] is True
        assert result.structured_content["keys_configured"] is False

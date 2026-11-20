"""The server against the PRD section 6 catalogue (PRD sections 5.2, 8.1 and 8.5).

Every registered tool must be in the catalogue and vice versa, come from its catalogue
module and carry the annotations for its kind. Every W and D tool refuses on the default
read-only server, and every D tool refuses without ``confirm=true``, before any network
call. No R tool returns the configured secrets, in results or in error text.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import boto3
import httpx
import pytest
import respx
from mcp.server.mcpserver import MCPServer

from tests.catalogue import CATALOGUE, TOTAL
from tests.conftest import (
    FAKE,
    FAKE_ACCESS_KEY,
    FAKE_SECRET_KEY,
    FAKE_WACM_API_KEY,
    FAKE_WACM_USERNAME,
    SECRETS,
)
from tests.helpers import ToolError, call
from wasabi_mcp.clients import Clients
from wasabi_mcp.config import Settings
from wasabi_mcp.server import build_server
from wasabi_mcp.tools import DESTRUCTIVE, READ, WRITE, ToolContext, discover_groups

# Valid as a bucket name, object key and IAM name; the leak test creates all three.
DUMMY = "catalogue-dummy"
ANNOTATIONS = {"R": READ, "W": WRITE, "D": DESTRUCTIVE}
KINDS = {name: kind for name, kind, _ in CATALOGUE}
MODULES = {name: module for name, _, module in CATALOGUE}


def _names(kind: str) -> list[str]:
    return [name for name, tool_kind, _ in CATALOGUE if tool_kind == kind]


async def _tool_map(server: MCPServer) -> dict[str, Any]:
    return {tool.name: tool for tool in await server.list_tools()}


def _tools(server: MCPServer) -> dict[str, Any]:
    return asyncio.run(_tool_map(server))


@pytest.fixture(scope="module")
def tools() -> dict[str, Any]:
    """The default server's tools by name, with fake credentials and nothing else set."""
    settings = Settings(access_key_id="AKIAFAKE", secret_access_key="fake")
    return _tools(build_server(settings))


def _dummy(schema: dict[str, Any]) -> Any:
    """A value that passes argument validation for a required parameter's schema."""
    if "enum" in schema:
        return schema["enum"][0]
    if "anyOf" in schema:
        return _dummy(schema["anyOf"][0])
    return {
        "string": DUMMY,
        "integer": 1,
        "number": 1,
        "boolean": False,
        "array": [],
        "object": {},
    }[schema["type"]]


def _arguments(tool: Any) -> dict[str, Any]:
    """Dummies for the required parameters, plus ``confirm=False`` where there is one."""
    schema = tool.input_schema
    arguments = {name: _dummy(schema["properties"][name]) for name in schema.get("required", [])}
    if "confirm" in schema["properties"]:
        arguments["confirm"] = False
    return arguments


def test_catalogue_lists_each_tool_once_and_totals_166():
    duplicates = [
        name for name, count in Counter(name for name, _, _ in CATALOGUE).items() if count > 1
    ]
    assert not duplicates
    assert len(CATALOGUE) == len(KINDS) == TOTAL == 166


def test_registered_tools_equal_the_catalogue(tools):
    missing = sorted(set(KINDS) - set(tools))
    extra = sorted(set(tools) - set(KINDS))
    assert not missing, f"catalogue tools missing from the server: {missing}"
    assert not extra, f"server registers tools the catalogue doesn't list: {extra}"
    assert len(tools) == TOTAL


def test_each_tool_comes_from_its_catalogue_module():
    settings = Settings(access_key_id="AKIAFAKE", secret_access_key="fake")
    ctx = ToolContext(settings=settings, clients=Clients(settings))
    registered: dict[str, str] = {}
    for group in discover_groups():
        server = MCPServer(group)
        importlib.import_module(f"wasabi_mcp.tools.{group}").register(server, ctx)
        registered.update(dict.fromkeys(_tools(server), group))
    wrong = {
        name: (registered.get(name), module)
        for name, module in MODULES.items()
        if registered.get(name) != module
    }
    assert not wrong, f"tool: (registered by, catalogue module): {wrong}"


@pytest.mark.parametrize(("name", "kind"), list(KINDS.items()))
def test_annotations_match_the_kind(tools, name, kind):
    assert tools[name].annotations == ANNOTATIONS[kind]


@pytest.mark.parametrize("name", _names("D"))
def test_destructive_tools_take_confirm_defaulting_to_false(tools, name):
    confirm = tools[name].input_schema["properties"].get("confirm")
    assert confirm is not None, f"{name} has no confirm parameter"
    assert confirm["type"] == "boolean"
    assert confirm["default"] is False
    assert "confirm" not in tools[name].input_schema.get("required", [])


@pytest.mark.parametrize("name", _names("R") + _names("W"))
def test_only_destructive_tools_take_confirm(tools, name):
    assert "confirm" not in tools[name].input_schema["properties"]


# --- network guards -------------------------------------------------------------------


@pytest.fixture
def sent(monkeypatch) -> Iterator[list[str]]:
    """Records, and fails, every HTTP request the server attempts (boto3 or httpx)."""
    attempts: list[str] = []

    def refuse(request: Any, **_: Any) -> Any:
        attempts.append(f"{request.method} {request.url}")
        raise AssertionError(f"unexpected network call: {request.method} {request.url}")

    original = Clients._client

    def patched(self, service, region, endpoint, config):
        client = original(self, service, region, endpoint, config)
        client.meta.events.register("before-send", refuse, unique_id="test-catalogue")
        return client

    monkeypatch.setattr(Clients, "_client", patched)
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(side_effect=refuse)
        yield attempts


@pytest.mark.parametrize("name", _names("W") + _names("D"))
async def test_writes_refused_on_the_default_read_only_server(rest_settings, tools, sent, name):
    # rest_settings has WACM configured, so WACM tools reach their guard.
    server = build_server(replace(rest_settings, read_only=True))
    arguments = _arguments(tools[name])
    if "confirm" in arguments:
        arguments["confirm"] = True
    with pytest.raises(ToolError, match="read-only"):
        await call(server, name, **arguments)
    assert sent == []


@pytest.mark.parametrize("name", _names("D"))
async def test_destructive_tools_refused_without_confirm(rest_server, tools, sent, name):
    with pytest.raises(ToolError, match="confirm=true"):
        await call(rest_server, name, **_arguments(tools[name]))
    assert sent == []


def test_default_settings_are_read_only():
    assert Settings(access_key_id="AKIAFAKE", secret_access_key="fake").read_only is True


# --- secrets never returned (PRD 8.5) -------------------------------------------------


@pytest.fixture
def leak_server(settings: Settings, moto_url: str) -> MCPServer:
    """S3, IAM and STS on moto with a bucket, object and user named like the dummies;
    Stats and WACM configured on ``FAKE`` hosts (respx). Writes allowed."""
    s3 = boto3.client(
        "s3",
        region_name="us-east-1",
        endpoint_url=moto_url,
        aws_access_key_id=FAKE_ACCESS_KEY,
        aws_secret_access_key=FAKE_SECRET_KEY,
    )
    s3.create_bucket(Bucket=DUMMY)
    s3.put_object(Bucket=DUMMY, Key=DUMMY, Body=b"x")
    iam = boto3.client(
        "iam",
        region_name="us-east-1",
        endpoint_url=moto_url,
        aws_access_key_id=FAKE_ACCESS_KEY,
        aws_secret_access_key=FAKE_SECRET_KEY,
    )
    iam.create_user(UserName=DUMMY)
    return build_server(
        replace(
            settings,
            wacm_username=FAKE_WACM_USERNAME,
            wacm_api_key=FAKE_WACM_API_KEY,
            stats_endpoint=FAKE["stats"],
            wacm_endpoint=FAKE["wacm"],
        )
    )


@pytest.mark.parametrize("status", [200, 403])
async def test_read_tools_never_return_the_configured_secrets(leak_server, status):
    tools = await _tool_map(leak_server)
    leaks: dict[str, str] = {}
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(return_value=httpx.Response(status, json={"message": "denied"}))
        for name in _names("R"):
            try:
                outcome: Any = await call(leak_server, name, **_arguments(tools[name]))
            except Exception as exc:  # error text counts too
                outcome = str(exc)
            text = outcome if isinstance(outcome, str) else json.dumps(outcome, default=str)
            if any(secret in text for secret in SECRETS):
                leaks[name] = text
    assert not leaks, f"tools returned configured secrets: {sorted(leaks)}"

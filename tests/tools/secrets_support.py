"""Shared bits for testing tools in WASABI_MCP_SECRETS_DIR mode."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as SDKToolError

from tests.helpers import ToolError, _text
from wasabi_mcp.config import Settings
from wasabi_mcp.server import build_server


def with_secrets_dir(settings: Settings, directory: Path) -> Settings:
    return replace(settings, secrets_dir=directory)


def secrets_server(settings: Settings, directory: Path) -> MCPServer:
    """A server built on ``settings`` that writes new secrets to ``directory``."""
    return build_server(with_secrets_dir(settings, directory))


async def call_with_text(server: MCPServer, tool: str, **arguments: Any) -> tuple[Any, str]:
    """Call ``tool``; return its structured result and everything the client saw as text."""
    try:
        result = await server.call_tool(tool, arguments)
    except SDKToolError as exc:
        raise ToolError(str(exc)) from exc
    if result.is_error:
        raise ToolError(_text(result.content))
    structured = result.structured_content
    text = json.dumps(structured, default=str) + _text(result.content)
    return structured, text


def read_secrets_file(path: str, directory: Path) -> dict[str, Any]:
    """The JSON in a secrets file, after checking it is a private file in ``directory``."""
    file = Path(path)
    assert file.is_absolute()
    assert file.parent == directory
    assert stat.S_IMODE(os.stat(file).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(directory).st_mode) == 0o700
    return json.loads(file.read_text())


def assert_absent(text: str, *values: str) -> None:
    """None of ``values`` appears in ``text``."""
    for value in values:
        assert value
        assert value not in text


UNUSABLE_DIRS = [
    pytest.param("shared", "chmod 700", id="group-and-world-readable"),
    pytest.param(
        "unwritable",
        "isn't writable",
        id="not-writable",
        marks=pytest.mark.skipif(os.geteuid() == 0, reason="root can write to any directory"),
    ),
]


def unusable_dir(tmp_path: Path, kind: str) -> Path:
    """An existing directory the server must refuse: ``shared`` (0755) or ``unwritable``."""
    directory = tmp_path / kind
    directory.mkdir()
    directory.chmod(0o755 if kind == "shared" else 0o500)
    return directory

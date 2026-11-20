"""Tool groups: one module per group, each exposing ``register(server, ctx)``.

Adding a module to this package adds its tools; no shared list is edited. ``meta.py`` is
the reference example. Every tool follows these conventions (PRD section 5):

- Register with ``@server.tool(name="<noun>_<verb>", annotations=READ|WRITE|DESTRUCTIVE)``.
  The docstring is the MCP description: one or two plain sentences on what the tool does
  and returns.
- W tools call ``require_writable(settings, "<Action>")`` first. D tools take
  ``confirm: bool = False`` and call ``require_confirmed(settings, "<Action>", confirm)``
  first. Guards run before any network call.
- Return plain JSON via ``clean()``. Lists return ``{"items", "next_token", "truncated"}``
  and take ``max_items`` and ``continuation_token``. Absent optional configuration returns
  ``{"configured": false}``.
- Turn botocore errors into ``raise explain(exc, "<Action>") from exc``; the ``rest``
  helpers already raise ``ToolRefused``.
- Never return the configured credentials.
"""

from __future__ import annotations

import base64
import pkgutil
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from mcp.types import ToolAnnotations

from wasabi_mcp.clients import Clients
from wasabi_mcp.config import Settings

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)

FIRST_GROUP = "meta"


@dataclass(frozen=True)
class ToolContext:
    """What every group's ``register`` receives."""

    settings: Settings
    clients: Clients


def discover_groups() -> list[str]:
    """Every module in this package: ``meta`` first, then alphabetical."""
    names = sorted(
        info.name
        for info in pkgutil.iter_modules(__path__)
        if not info.ispkg and not info.name.startswith("_")
    )
    return sorted(names, key=lambda name: name != FIRST_GROUP)


def clean(value: Any) -> Any:
    """Make a boto3 response JSON-ready.

    Drops ``ResponseMetadata``, turns datetimes into ISO 8601 strings and decodes bytes
    (UTF-8, or base64 when the bytes aren't text).
    """
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items() if k != "ResponseMetadata"}
    if isinstance(value, list | tuple | set | frozenset):
        return [clean(item) for item in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes | bytearray):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return base64.b64encode(value).decode("ascii")
    return value

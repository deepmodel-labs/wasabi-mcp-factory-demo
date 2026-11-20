"""The server's one error helper module: what the client sees when something fails.

``explain`` turns botocore exceptions into client-facing ``ToolRefused`` errors.
``guard`` wraps every tool (applied once, in ``server.build_server``): deliberate errors
pass through unchanged, and anything unexpected becomes a short ``ToolRefused`` naming the
tool while the redacted traceback goes to the ``wasabi_mcp`` logger (stderr).
"""

from __future__ import annotations

import functools
import inspect
import logging
import re
import traceback
from collections.abc import Callable
from typing import Any

from botocore.exceptions import ClientError
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.shared.exceptions import MCPError

from wasabi_mcp.config import Settings
from wasabi_mcp.safety import ToolRefused

logger = logging.getLogger("wasabi_mcp")

REDACTED = "[REDACTED]"

_SECRET_NAMES = (
    r"SecretAccessKey|secret_access_key|SessionToken|session_token|"
    r"X-Amz-Security-Token|X-Amz-Signature|X-Amz-Credential"
)
# ``Name=value`` (query string, key=value), ``Name: value`` (header) and
# ``"Name": "value"`` / ``'Name': 'value'`` (JSON, dict repr). The value ends at a
# quote, whitespace or a separator.
_SECRET_VALUE = re.compile(
    rf"""((?:{_SECRET_NAMES})['"]?\s*[:=]\s*['"]?)[^'"\s&,;}}]+""", re.IGNORECASE
)
# An ``Authorization`` value has spaces (``AWS4-HMAC-SHA256 Credential=..., Signature=...``),
# so it runs to the closing quote or the end of the line.
_AUTHORIZATION = re.compile(r"""(Authorization['"]?\s*[:=]\s*['"]?)[^'"\r\n]+""", re.IGNORECASE)


def explain(exc: Exception, action: str) -> ToolRefused:
    """Return a ``ToolRefused`` saying ``"<action> failed: <Code>: <Message>"``.

    Use as ``raise explain(exc, "GetBucketPolicy") from exc``.
    """
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        code = error.get("Code") or "Unknown"
        message = error.get("Message") or str(exc)
        return ToolRefused(f"{action} failed: {code}: {message}")
    # BotoCoreError (connection, credential and parameter errors) has no code:
    # its class name says what kind of failure it was.
    return ToolRefused(f"{action} failed: {type(exc).__name__}: {exc}")


def redact(text: str, settings: Settings) -> str:
    """``text`` with the configured credentials and common secret values replaced."""
    configured = (settings.access_key_id, settings.secret_access_key, settings.wacm_api_key)
    for secret in sorted(filter(None, configured), key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    text = _AUTHORIZATION.sub(rf"\1{REDACTED}", text)
    return _SECRET_VALUE.sub(rf"\1{REDACTED}", text)


def unexpected(exc: BaseException, tool: str, settings: Settings) -> ToolRefused:
    """Log ``exc`` (redacted, with its traceback) and return the client's short message.

    Use as ``raise unexpected(exc, "object_get", settings) from None``. The traceback is
    formatted into the message rather than passed as ``exc_info``, so no handler renders
    the raw exception.
    """
    details = redact("".join(traceback.format_exception(exc)), settings)
    logger.error("Tool %r raised an unexpected exception:\n%s", tool, details)
    return ToolRefused(f"{tool} failed: unexpected internal error (details are in the server log)")


def guard(fn: Callable[..., Any], tool: str, settings: Settings) -> Callable[..., Any]:
    """``fn`` with unexpected exceptions turned into ``unexpected(...)``.

    ``ToolError`` (so every ``ToolRefused``), ``ResourceError`` and ``MCPError`` pass
    through unchanged. The signature is kept (``functools.wraps``), so the SDK derives
    the same input schema, and a coroutine function stays one.
    """
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def guarded_async(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except (ToolError, ResourceError, MCPError):
                raise
            except Exception as exc:
                raise unexpected(exc, tool, settings) from None

        return guarded_async

    @functools.wraps(fn)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (ToolError, ResourceError, MCPError):
            raise
        except Exception as exc:
            raise unexpected(exc, tool, settings) from None

    return guarded

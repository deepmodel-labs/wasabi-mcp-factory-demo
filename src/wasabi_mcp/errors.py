"""Turn botocore exceptions into client-facing ``ToolRefused`` errors."""

from __future__ import annotations

from botocore.exceptions import ClientError

from wasabi_mcp.safety import ToolRefused


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

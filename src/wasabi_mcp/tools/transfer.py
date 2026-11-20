"""Presigned URLs (PRD section 6.5): GET, PUT and GET for every match under a prefix.

Large transfers go through these URLs so object data never passes through the model.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from mcp.server.mcpserver import MCPServer

from wasabi_mcp.errors import explain
from wasabi_mcp.safety import ToolRefused, require_writable
from wasabi_mcp.tools import READ, WRITE, ToolContext, clean
from wasabi_mcp.tools.objects_read import DEFAULT_MAX_ITEMS, _check_max_items, _s3, _size_human

DEFAULT_EXPIRES = 3_600
MIN_EXPIRES = 60
# Wasabi's maximum lifetime for URLs signed with IAM user keys (7 days).
MAX_EXPIRES = 604_800
# ListObjectsV2 page size for presign_get_for_prefix; tests lower it to force paging.
_LIST_PAGE_SIZE = 1_000


def _check_expires(expires_seconds: int) -> int:
    if not MIN_EXPIRES <= expires_seconds <= MAX_EXPIRES:
        raise ToolRefused(
            f"expires_seconds must be between {MIN_EXPIRES} and {MAX_EXPIRES} (7 days), "
            f"got {expires_seconds}."
        )
    return expires_seconds


def _expires_at(expires_seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=expires_seconds)).isoformat()


def _presign(client: Any, operation: str, params: dict[str, Any], expires_seconds: int) -> str:
    try:
        return client.generate_presigned_url(operation, Params=params, ExpiresIn=expires_seconds)
    except (ClientError, BotoCoreError) as exc:
        raise explain(exc, "Presign") from exc


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings

    @server.tool(name="presign_get", annotations=READ)
    def presign_get(
        bucket: str,
        key: str,
        expires_seconds: int = DEFAULT_EXPIRES,
        version_id: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        """A presigned URL that downloads an object without credentials, valid for
        expires_seconds (60 to 604,800; default 3,600). Use it for large or binary
        objects."""
        _check_expires(expires_seconds)
        params = {"Bucket": bucket, "Key": key}
        if version_id:
            params["VersionId"] = version_id
        url = _presign(_s3(ctx, bucket, region), "get_object", params, expires_seconds)
        return {
            "bucket": bucket,
            "key": key,
            "method": "GET",
            "url": url,
            "expires_seconds": expires_seconds,
            "expires_at": _expires_at(expires_seconds),
        }

    @server.tool(name="presign_put", annotations=WRITE)
    def presign_put(
        bucket: str,
        key: str,
        expires_seconds: int = DEFAULT_EXPIRES,
        content_type: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        """A presigned URL that lets anyone holding it upload to this key with HTTP PUT,
        valid for expires_seconds (60 to 604,800; default 3,600). With content_type, the
        upload must send that Content-Type header."""
        require_writable(settings, "PresignPutObject")
        _check_expires(expires_seconds)
        params = {"Bucket": bucket, "Key": key}
        if content_type:
            params["ContentType"] = content_type
        url = _presign(_s3(ctx, bucket, region), "put_object", params, expires_seconds)
        result = {
            "bucket": bucket,
            "key": key,
            "method": "PUT",
            "url": url,
            "expires_seconds": expires_seconds,
            "expires_at": _expires_at(expires_seconds),
        }
        if content_type:
            result["headers"] = {"Content-Type": content_type}
        return result

    @server.tool(name="presign_get_for_prefix", annotations=READ)
    def presign_get_for_prefix(
        bucket: str,
        prefix: str,
        suffix: str | None = None,
        expires_seconds: int = DEFAULT_EXPIRES,
        max_items: int = DEFAULT_MAX_ITEMS,
        continuation_token: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        """Presigned download URLs for every object under prefix whose key ends with
        suffix (for example .mp4), at most max_items (default 100, at most 1,000) per
        call. Each item has key, size and url; pass next_token for more."""
        _check_expires(expires_seconds)
        _check_max_items(max_items)
        client = _s3(ctx, bucket, region)
        # The token is the last key looked at, so each page (and the next call) starts
        # right after it.
        last_key = continuation_token or None
        items: list[dict[str, Any]] = []
        exhausted = False
        while not exhausted and len(items) < max_items:
            args: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": _LIST_PAGE_SIZE}
            if last_key:
                args["StartAfter"] = last_key
            try:
                response = client.list_objects_v2(**args)
            except (ClientError, BotoCoreError) as exc:
                raise explain(exc, "ListObjectsV2") from exc
            contents = response.get("Contents", [])
            exhausted = not contents
            for index, entry in enumerate(contents):
                last_key = entry["Key"]
                exhausted = index == len(contents) - 1 and not response.get("IsTruncated")
                if suffix and not last_key.endswith(suffix):
                    continue
                url = _presign(
                    client, "get_object", {"Bucket": bucket, "Key": last_key}, expires_seconds
                )
                size = entry.get("Size")
                items.append(
                    {"key": last_key, "size": size, "size_human": _size_human(size), "url": url}
                )
                if len(items) >= max_items:
                    break
        truncated = not exhausted
        return clean(
            {
                "items": items,
                "next_token": last_key if truncated else None,
                "truncated": truncated,
                "expires_seconds": expires_seconds,
                "expires_at": _expires_at(expires_seconds),
            }
        )

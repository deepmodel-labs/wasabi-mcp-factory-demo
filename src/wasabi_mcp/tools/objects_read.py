"""Object reads (PRD section 6.2): list, versions, head, capped text, tagging, search.

Also holds the bucket-region helpers the other object modules import.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any
from weakref import WeakKeyDictionary

from botocore.exceptions import BotoCoreError, ClientError
from mcp.server.mcpserver import MCPServer

from wasabi_mcp.clients import Clients
from wasabi_mcp.errors import explain
from wasabi_mcp.safety import ToolRefused
from wasabi_mcp.tools import READ, ToolContext, clean

DEFAULT_MAX_ITEMS = 100
MAX_ITEMS_LIMIT = 1_000
DEFAULT_TEXT_BYTES = 262_144
TEXT_BYTES_LIMIT = 1_048_576
DEFAULT_MAX_SCAN = 10_000
MAX_SCAN_LIMIT = 100_000
US_EAST_1 = "us-east-1"
PRESIGN_HINT = "Use presign_get to download it instead."

# Bucket name -> region, kept per ``Clients`` so separate servers never share entries.
_REGIONS: WeakKeyDictionary[Clients, dict[str, str]] = WeakKeyDictionary()


def _bucket_region(ctx: ToolContext, bucket: str, region: str | None = None) -> str:
    """``region`` if given, else the bucket's region from GetBucketLocation (cached)."""
    if region:
        return region
    cache = _REGIONS.setdefault(ctx.clients, {})
    if bucket not in cache:
        try:
            response = ctx.clients.s3().get_bucket_location(Bucket=bucket)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "GetBucketLocation") from exc
        cache[bucket] = response.get("LocationConstraint") or US_EAST_1
    return cache[bucket]


def _s3(ctx: ToolContext, bucket: str, region: str | None = None) -> Any:
    """An S3 client on the endpoint for ``bucket``'s region."""
    return ctx.clients.s3(_bucket_region(ctx, bucket, region))


def _size_human(size: int | None) -> str | None:
    """``1234567`` -> ``"1.2 MB"`` (decimal units)."""
    if size is None:
        return None
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < 1000 or unit == "PB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return None  # unreachable


def _check_max_items(max_items: int) -> int:
    if not 1 <= max_items <= MAX_ITEMS_LIMIT:
        raise ToolRefused(f"max_items must be between 1 and {MAX_ITEMS_LIMIT}, got {max_items}.")
    return max_items


def _page_args(max_items: int, continuation_token: str | None) -> dict[str, Any]:
    """ListObjectsV2 paging arguments, after checking ``max_items`` is 1 to 1,000."""
    args: dict[str, Any] = {"MaxKeys": _check_max_items(max_items)}
    if continuation_token:
        args["ContinuationToken"] = continuation_token
    return args


def _object_item(entry: dict[str, Any]) -> dict[str, Any]:
    size = entry.get("Size")
    return clean(
        {
            "key": entry.get("Key"),
            "size": size,
            "size_human": _size_human(size),
            "last_modified": entry.get("LastModified"),
            "etag": entry.get("ETag"),
            "storage_class": entry.get("StorageClass"),
        }
    )


def _encode_marker(key: str | None, version_id: str | None) -> str | None:
    if key is None:
        return None
    raw = json.dumps({"k": key, "v": version_id}).encode()
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_marker(token: str) -> dict[str, str]:
    try:
        data = json.loads(base64.urlsafe_b64decode(token.encode("ascii")))
    except (ValueError, UnicodeError) as exc:
        raise ToolRefused("continuation_token is not a token from object_version_list.") from exc
    args = {"KeyMarker": data["k"]}
    if data.get("v"):
        args["VersionIdMarker"] = data["v"]
    return args


def _total_size(response: dict[str, Any]) -> int | None:
    content_range = response.get("ContentRange") or ""
    _, _, total = content_range.rpartition("/")
    if total.isdigit():
        return int(total)
    return response.get("ContentLength")


def _decode_text(body: bytes, truncated: bool) -> str | None:
    """UTF-8 text, or ``None`` for binary. A cut multi-byte character at the end is dropped."""
    if b"\x00" in body:
        return None
    # A truncated body may end inside a character of up to 4 bytes.
    for trim in range(4 if truncated else 1):
        try:
            return body[: len(body) - trim].decode("utf-8")
        except UnicodeDecodeError:
            continue
    return None


def _parse_time(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ToolRefused(f"{name} must be an ISO 8601 date or time, got {value!r}.") from exc
    return parsed if parsed.tzinfo else parsed.astimezone()


def _matches(
    entry: dict[str, Any],
    *,
    suffix: str | None,
    contains: str | None,
    min_size: int | None,
    max_size: int | None,
    after: datetime | None,
    before: datetime | None,
) -> bool:
    key: str = entry["Key"]
    size: int = entry.get("Size", 0)
    modified: datetime | None = entry.get("LastModified")
    return (
        (suffix is None or key.endswith(suffix))
        and (contains is None or contains in key)
        and (min_size is None or size >= min_size)
        and (max_size is None or size <= max_size)
        and (after is None or (modified is not None and modified >= after))
        and (before is None or (modified is not None and modified < before))
    )


def register(server: MCPServer, ctx: ToolContext) -> None:
    @server.tool(name="object_list", annotations=READ)
    def object_list(
        bucket: str,
        prefix: str | None = None,
        delimiter: str | None = None,
        start_after: str | None = None,
        max_items: int = DEFAULT_MAX_ITEMS,
        continuation_token: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        """List objects in a bucket, optionally under a prefix. Each item has key, size,
        last modified and ETag; with a delimiter, folder-like prefixes come back in
        common_prefixes. Paginate with next_token."""
        args = {"Bucket": bucket, **_page_args(max_items, continuation_token)}
        for name, value in (
            ("Prefix", prefix),
            ("Delimiter", delimiter),
            ("StartAfter", start_after),
        ):
            if value:
                args[name] = value
        try:
            response = _s3(ctx, bucket, region).list_objects_v2(**args)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "ListObjectsV2") from exc
        return {
            "items": [_object_item(entry) for entry in response.get("Contents", [])],
            "common_prefixes": [p["Prefix"] for p in response.get("CommonPrefixes", [])],
            "next_token": response.get("NextContinuationToken"),
            "truncated": bool(response.get("IsTruncated")),
        }

    @server.tool(name="object_version_list", annotations=READ)
    def object_version_list(
        bucket: str,
        prefix: str | None = None,
        max_items: int = DEFAULT_MAX_ITEMS,
        continuation_token: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        """List object versions and delete markers in a bucket, optionally under a prefix.
        Each item says whether it is the latest version or a delete marker. Paginate with
        next_token."""
        args: dict[str, Any] = {"Bucket": bucket, "MaxKeys": _check_max_items(max_items)}
        if prefix:
            args["Prefix"] = prefix
        if continuation_token:
            args.update(_decode_marker(continuation_token))
        try:
            response = _s3(ctx, bucket, region).list_object_versions(**args)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "ListObjectVersions") from exc
        versions = [
            {
                **_object_item(v),
                "version_id": v.get("VersionId"),
                "is_latest": v.get("IsLatest"),
                "delete_marker": False,
            }
            for v in response.get("Versions", [])
        ]
        markers = [
            clean(
                {
                    "key": m.get("Key"),
                    "version_id": m.get("VersionId"),
                    "is_latest": m.get("IsLatest"),
                    "last_modified": m.get("LastModified"),
                    "delete_marker": True,
                }
            )
            for m in response.get("DeleteMarkers", [])
        ]
        items = sorted(versions + markers, key=lambda item: item["key"])
        truncated = bool(response.get("IsTruncated"))
        next_token = (
            _encode_marker(response.get("NextKeyMarker"), response.get("NextVersionIdMarker"))
            if truncated
            else None
        )
        return {"items": items, "next_token": next_token, "truncated": truncated}

    @server.tool(name="object_get_info", annotations=READ)
    def object_get_info(
        bucket: str,
        key: str,
        version_id: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        """Show an object's size, content type, ETag, last modified time, user metadata,
        storage class, version and any Object Lock retention or legal hold, without
        downloading it."""
        args = {"Bucket": bucket, "Key": key}
        if version_id:
            args["VersionId"] = version_id
        try:
            response = _s3(ctx, bucket, region).head_object(**args)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "HeadObject") from exc
        size = response.get("ContentLength")
        info = {
            "bucket": bucket,
            "key": key,
            "size": size,
            "size_human": _size_human(size),
            "content_type": response.get("ContentType"),
            "etag": response.get("ETag"),
            "last_modified": response.get("LastModified"),
            "metadata": response.get("Metadata", {}),
            "storage_class": response.get("StorageClass"),
            "version_id": response.get("VersionId"),
            "cache_control": response.get("CacheControl"),
            "content_encoding": response.get("ContentEncoding"),
            "lock_mode": response.get("ObjectLockMode"),
            "lock_retain_until": response.get("ObjectLockRetainUntilDate"),
            "legal_hold": response.get("ObjectLockLegalHoldStatus"),
        }
        return clean({name: value for name, value in info.items() if value is not None})

    @server.tool(name="object_get_text", annotations=READ)
    def object_get_text(
        bucket: str,
        key: str,
        max_bytes: int = DEFAULT_TEXT_BYTES,
        version_id: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        """Read a text object as UTF-8, up to max_bytes (default 256 KiB, at most 1 MiB).
        Larger objects are truncated and the result says so; binary objects are refused.
        Use presign_get for large or binary downloads."""
        if not 1 <= max_bytes <= TEXT_BYTES_LIMIT:
            raise ToolRefused(
                f"max_bytes must be between 1 and {TEXT_BYTES_LIMIT} (1 MiB), got {max_bytes}. "
                + PRESIGN_HINT
            )
        args = {"Bucket": bucket, "Key": key, "Range": f"bytes=0-{max_bytes}"}
        if version_id:
            args["VersionId"] = version_id
        client = _s3(ctx, bucket, region)
        try:
            response = client.get_object(**args)
            body: bytes = response["Body"].read()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "InvalidRange":
                raise explain(exc, "GetObject") from exc
            # An empty object has no byte 0, so a ranged read of it fails.
            response, body = {"ContentLength": 0}, b""
        except BotoCoreError as exc:
            raise explain(exc, "GetObject") from exc
        except Exception as exc:
            # A botocore bug while streaming the body must not reach the client unhandled.
            raise ToolRefused(
                f"GetObject failed while reading the object body: {type(exc).__name__}."
            ) from exc
        size = _total_size(response)
        truncated = len(body) > max_bytes
        text = _decode_text(body[:max_bytes], truncated)
        if text is None:
            raise ToolRefused(f"{key} is not UTF-8 text (it looks binary). {PRESIGN_HINT}")
        result = {
            "bucket": bucket,
            "key": key,
            "text": text,
            "size": size,
            "size_human": _size_human(size),
            "returned_bytes": len(text.encode("utf-8")),
            "content_type": response.get("ContentType"),
            "truncated": truncated,
        }
        if truncated:
            result["note"] = (
                f"Truncated: showing the first {result['returned_bytes']} of {size} bytes. "
                + PRESIGN_HINT
            )
        return clean({name: value for name, value in result.items() if value is not None})

    @server.tool(name="object_tagging_get", annotations=READ)
    def object_tagging_get(
        bucket: str,
        key: str,
        version_id: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        """Show an object's tags as a key-value map, or configured: false when it has none."""
        args = {"Bucket": bucket, "Key": key}
        if version_id:
            args["VersionId"] = version_id
        try:
            response = _s3(ctx, bucket, region).get_object_tagging(**args)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "GetObjectTagging") from exc
        tags = {tag["Key"]: tag["Value"] for tag in response.get("TagSet", [])}
        if not tags:
            return {"configured": False}
        return {"configured": True, "tags": tags}

    @server.tool(name="object_search", annotations=READ)
    def object_search(
        bucket: str,
        prefix: str | None = None,
        suffix: str | None = None,
        contains: str | None = None,
        min_size: int | None = None,
        max_size: int | None = None,
        modified_after: str | None = None,
        modified_before: str | None = None,
        max_items: int = DEFAULT_MAX_ITEMS,
        max_scan: int = DEFAULT_MAX_SCAN,
        continuation_token: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        """Find objects under a prefix by key suffix, key substring, size range or
        modification time (ISO 8601). Scans at most max_scan keys (default 10,000) and
        says when it stopped early; pass next_token to resume."""
        _check_max_items(max_items)
        if not 1 <= max_scan <= MAX_SCAN_LIMIT:
            raise ToolRefused(f"max_scan must be between 1 and {MAX_SCAN_LIMIT}, got {max_scan}.")
        after = _parse_time(modified_after, "modified_after")
        before = _parse_time(modified_before, "modified_before")
        client = _s3(ctx, bucket, region)
        # The token is the last key scanned, so each page (and the next call) starts
        # right after it.
        last_key = continuation_token or None
        items: list[dict[str, Any]] = []
        scanned = 0
        exhausted = False
        while not exhausted and scanned < max_scan and len(items) < max_items:
            args: dict[str, Any] = {
                "Bucket": bucket,
                "MaxKeys": min(MAX_ITEMS_LIMIT, max_scan - scanned),
            }
            if prefix:
                args["Prefix"] = prefix
            if last_key:
                args["StartAfter"] = last_key
            try:
                response = client.list_objects_v2(**args)
            except (ClientError, BotoCoreError) as exc:
                raise explain(exc, "ListObjectsV2") from exc
            contents = response.get("Contents", [])
            for index, entry in enumerate(contents):
                scanned += 1
                last_key = entry["Key"]
                exhausted = index == len(contents) - 1 and not response.get("IsTruncated")
                if _matches(
                    entry,
                    suffix=suffix,
                    contains=contains,
                    min_size=min_size,
                    max_size=max_size,
                    after=after,
                    before=before,
                ):
                    items.append(_object_item(entry))
                    if len(items) >= max_items:
                        break
            if not contents:
                exhausted = True
        truncated = not exhausted
        result: dict[str, Any] = {
            "items": items,
            "next_token": last_key if truncated else None,
            "truncated": truncated,
            "scanned": scanned,
        }
        if truncated and scanned >= max_scan and len(items) < max_items:
            result["note"] = (
                f"Stopped early after scanning {scanned} keys (max_scan). "
                "Pass next_token to keep searching."
            )
        return result

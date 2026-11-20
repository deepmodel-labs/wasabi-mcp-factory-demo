"""Presigned URLs and multipart (PRD section 6.5): GET, PUT and GET for every match
under a prefix; list, list parts, abort, presign parts and complete multipart uploads.

Large transfers go through these URLs so object data never passes through the model.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from mcp.server.mcpserver import MCPServer

from wasabi_mcp.errors import explain
from wasabi_mcp.safety import ToolRefused, require_confirmed, require_writable
from wasabi_mcp.tools import DESTRUCTIVE, READ, WRITE, ToolContext, clean
from wasabi_mcp.tools.objects_read import DEFAULT_MAX_ITEMS, _check_max_items, _s3, _size_human

DEFAULT_EXPIRES = 3_600
MIN_EXPIRES = 60
# Wasabi's maximum lifetime for URLs signed with IAM user keys (7 days).
MAX_EXPIRES = 604_800
# ListObjectsV2 page size for presign_get_for_prefix; tests lower it to force paging.
_LIST_PAGE_SIZE = 1_000
MAX_PARTS = 10_000


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


def _check_part_count(part_count: int) -> int:
    if not 1 <= part_count <= MAX_PARTS:
        raise ToolRefused(f"part_count must be between 1 and {MAX_PARTS}, got {part_count}.")
    return part_count


def _encode_upload_marker(key: str | None, upload_id: str | None) -> str | None:
    if not key:
        return None
    raw = json.dumps({"k": key, "u": upload_id}).encode()
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_upload_marker(token: str) -> dict[str, str]:
    try:
        data = json.loads(base64.urlsafe_b64decode(token.encode("ascii")))
        args = {"KeyMarker": data["k"]}
    except (ValueError, UnicodeError, KeyError, TypeError) as exc:
        raise ToolRefused("continuation_token is not a token from multipart_upload_list.") from exc
    if data.get("u"):
        args["UploadIdMarker"] = data["u"]
    return args


def _check_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not 1 <= len(parts) <= MAX_PARTS:
        raise ToolRefused(f"parts must list 1 to {MAX_PARTS} parts, got {len(parts)}.")
    checked = []
    for part in parts:
        number, etag = part.get("part_number"), part.get("etag")
        if not isinstance(number, int) or not 1 <= number <= MAX_PARTS or not etag:
            raise ToolRefused(
                "Each part needs part_number (1 to 10,000) and the etag its upload returned."
            )
        checked.append({"PartNumber": number, "ETag": etag})
    return sorted(checked, key=lambda part: part["PartNumber"])


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

    @server.tool(name="multipart_upload_list", annotations=READ)
    def multipart_upload_list(
        bucket: str,
        prefix: str | None = None,
        max_items: int = DEFAULT_MAX_ITEMS,
        continuation_token: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        """List incomplete multipart uploads in a bucket, optionally under a prefix. Each
        item has key, upload_id and initiated time. Wasabi deletes incomplete uploads
        after about 31 days. Paginate with next_token."""
        _check_max_items(max_items)
        args: dict[str, Any] = {"Bucket": bucket, "MaxUploads": max_items}
        if prefix:
            args["Prefix"] = prefix
        if continuation_token:
            args.update(_decode_upload_marker(continuation_token))
        try:
            response = _s3(ctx, bucket, region).list_multipart_uploads(**args)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "ListMultipartUploads") from exc
        truncated = bool(response.get("IsTruncated"))
        items = [
            {
                "key": upload.get("Key"),
                "upload_id": upload.get("UploadId"),
                "initiated": upload.get("Initiated"),
                "storage_class": upload.get("StorageClass"),
            }
            for upload in response.get("Uploads", [])
        ]
        next_token = (
            _encode_upload_marker(response.get("NextKeyMarker"), response.get("NextUploadIdMarker"))
            if truncated
            else None
        )
        return clean({"items": items, "next_token": next_token, "truncated": truncated})

    @server.tool(name="multipart_part_list", annotations=READ)
    def multipart_part_list(
        bucket: str,
        key: str,
        upload_id: str,
        max_items: int = DEFAULT_MAX_ITEMS,
        continuation_token: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        """List the parts uploaded so far for a multipart upload. Each item has
        part_number, etag, size and last modified. Paginate with next_token."""
        _check_max_items(max_items)
        args: dict[str, Any] = {
            "Bucket": bucket,
            "Key": key,
            "UploadId": upload_id,
            "MaxParts": max_items,
        }
        if continuation_token:
            if not continuation_token.isdigit():
                raise ToolRefused("continuation_token is not a token from multipart_part_list.")
            args["PartNumberMarker"] = int(continuation_token)
        try:
            response = _s3(ctx, bucket, region).list_parts(**args)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "ListParts") from exc
        truncated = bool(response.get("IsTruncated"))
        items = [
            {
                "part_number": part.get("PartNumber"),
                "etag": part.get("ETag"),
                "size": part.get("Size"),
                "size_human": _size_human(part.get("Size")),
                "last_modified": part.get("LastModified"),
            }
            for part in response.get("Parts", [])
        ]
        marker = response.get("NextPartNumberMarker")
        next_token = str(marker) if truncated and marker else None
        return clean({"items": items, "next_token": next_token, "truncated": truncated})

    @server.tool(name="multipart_upload_abort", annotations=DESTRUCTIVE)
    def multipart_upload_abort(
        bucket: str,
        key: str,
        upload_id: str,
        confirm: bool = False,
        region: str | None = None,
    ) -> dict[str, Any]:
        """Abort a multipart upload and discard every part uploaded for it. Needs
        confirm=true."""
        require_confirmed(settings, "AbortMultipartUpload", confirm)
        try:
            _s3(ctx, bucket, region).abort_multipart_upload(
                Bucket=bucket, Key=key, UploadId=upload_id
            )
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "AbortMultipartUpload") from exc
        return {"bucket": bucket, "key": key, "upload_id": upload_id, "aborted": True}

    @server.tool(name="multipart_upload_presign", annotations=WRITE)
    def multipart_upload_presign(
        bucket: str,
        key: str,
        part_count: int,
        expires_seconds: int = DEFAULT_EXPIRES,
        content_type: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        """Start a multipart upload and return its upload_id and one presigned PUT URL
        per part (part_count 1 to 10,000), valid for expires_seconds (60 to 604,800).
        Upload each part with HTTP PUT, keep each response's ETag, then call
        multipart_upload_complete."""
        require_writable(settings, "CreateMultipartUpload")
        _check_part_count(part_count)
        _check_expires(expires_seconds)
        client = _s3(ctx, bucket, region)
        args = {"Bucket": bucket, "Key": key}
        if content_type:
            args["ContentType"] = content_type
        try:
            upload_id = client.create_multipart_upload(**args)["UploadId"]
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "CreateMultipartUpload") from exc
        parts = [
            {
                "part_number": number,
                "url": _presign(
                    client,
                    "upload_part",
                    {"Bucket": bucket, "Key": key, "UploadId": upload_id, "PartNumber": number},
                    expires_seconds,
                ),
            }
            for number in range(1, part_count + 1)
        ]
        return {
            "bucket": bucket,
            "key": key,
            "upload_id": upload_id,
            "method": "PUT",
            "parts": parts,
            "expires_seconds": expires_seconds,
            "expires_at": _expires_at(expires_seconds),
        }

    @server.tool(name="multipart_upload_complete", annotations=WRITE)
    def multipart_upload_complete(
        bucket: str,
        key: str,
        upload_id: str,
        parts: list[dict[str, Any]],
        region: str | None = None,
    ) -> dict[str, Any]:
        """Complete a multipart upload from its parts, each {part_number, etag} with the
        ETag its upload returned. Returns the new object's key and ETag."""
        require_writable(settings, "CompleteMultipartUpload")
        checked = _check_parts(parts)
        try:
            response = _s3(ctx, bucket, region).complete_multipart_upload(
                Bucket=bucket, Key=key, UploadId=upload_id, MultipartUpload={"Parts": checked}
            )
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "CompleteMultipartUpload") from exc
        return clean(
            {
                "bucket": bucket,
                "key": key,
                "etag": response.get("ETag"),
                "version_id": response.get("VersionId"),
            }
        )

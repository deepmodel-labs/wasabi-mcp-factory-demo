"""Object writes (PRD section 6.3): small text puts, copies, deletes, tags and metadata."""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from mcp.server.mcpserver import MCPServer

from wasabi_mcp.errors import explain
from wasabi_mcp.safety import ToolRefused, require_confirmed, require_writable
from wasabi_mcp.tools import DESTRUCTIVE, WRITE, ToolContext, clean
from wasabi_mcp.tools.objects_read import TEXT_BYTES_LIMIT, _s3

DEFAULT_TEXT_TYPE = "text/plain; charset=utf-8"
DELETE_BATCH = 1_000
MAX_DELETE_BY_PREFIX = 10_000
NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}


def _exists(client: Any, bucket: str, key: str) -> bool:
    """Whether ``key`` exists, via HeadObject."""
    try:
        client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in NOT_FOUND_CODES:
            return False
        raise explain(exc, "HeadObject") from exc
    except BotoCoreError as exc:
        raise explain(exc, "HeadObject") from exc
    return True


def _refuse_if_exists(client: Any, bucket: str, key: str, action: str) -> None:
    if _exists(client, bucket, key):
        raise ToolRefused(
            f"{action} refused: {bucket}/{key} already exists. "
            "Call again with overwrite=true to replace it."
        )


def _keys_under(client: Any, bucket: str, prefix: str, limit: int) -> tuple[list[str], bool]:
    """Up to ``limit`` keys under ``prefix``, and whether more remain."""
    keys: list[str] = []
    args: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
    while True:
        args["MaxKeys"] = min(DELETE_BATCH, limit - len(keys) + 1)
        try:
            response = client.list_objects_v2(**args)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "ListObjectsV2") from exc
        keys.extend(entry["Key"] for entry in response.get("Contents", []))
        if len(keys) > limit:
            return keys[:limit], True
        if not response.get("IsTruncated"):
            return keys, False
        args["ContinuationToken"] = response["NextContinuationToken"]


def _delete_batches(client: Any, bucket: str, keys: list[str]) -> dict[str, list[Any]]:
    deleted: list[str] = []
    errors: list[dict[str, Any]] = []
    for start in range(0, len(keys), DELETE_BATCH):
        batch = keys[start : start + DELETE_BATCH]
        try:
            response = client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": key} for key in batch], "Quiet": False},
            )
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "DeleteObjects") from exc
        deleted.extend(item["Key"] for item in response.get("Deleted", []))
        errors.extend(
            {"key": item.get("Key"), "code": item.get("Code"), "message": item.get("Message")}
            for item in response.get("Errors", [])
        )
    return {"deleted": deleted, "errors": errors}


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings

    @server.tool(name="object_put_text", annotations=WRITE)
    def object_put_text(
        bucket: str,
        key: str,
        text: str,
        content_type: str = DEFAULT_TEXT_TYPE,
        metadata: dict[str, str] | None = None,
        overwrite: bool = False,
        region: str | None = None,
    ) -> dict[str, Any]:
        """Write a small UTF-8 text object (at most 1 MiB) with an optional content type
        and user metadata. Refuses when the key exists unless overwrite is true; use
        presign_put for larger uploads."""
        require_writable(settings, "PutObject")
        body = text.encode("utf-8")
        if len(body) > TEXT_BYTES_LIMIT:
            raise ToolRefused(
                f"PutObject refused: the text is {len(body)} bytes, over the "
                f"{TEXT_BYTES_LIMIT}-byte (1 MiB) limit. Use presign_put to upload it instead."
            )
        client = _s3(ctx, bucket, region)
        if not overwrite:
            _refuse_if_exists(client, bucket, key, "PutObject")
        args: dict[str, Any] = {
            "Bucket": bucket,
            "Key": key,
            "Body": body,
            "ContentType": content_type,
        }
        if metadata:
            args["Metadata"] = metadata
        try:
            response = client.put_object(**args)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "PutObject") from exc
        return clean(
            {
                "bucket": bucket,
                "key": key,
                "size": len(body),
                "etag": response.get("ETag"),
                "version_id": response.get("VersionId"),
            }
        )

    @server.tool(name="object_copy", annotations=WRITE)
    def object_copy(
        source_bucket: str,
        source_key: str,
        destination_bucket: str,
        destination_key: str,
        version_id: str | None = None,
        metadata: dict[str, str] | None = None,
        content_type: str | None = None,
        overwrite: bool = False,
        region: str | None = None,
    ) -> dict[str, Any]:
        """Copy an object within a bucket or to another bucket in the same region, server
        side. Refuses when the destination exists unless overwrite is true. Passing
        metadata or content_type replaces the copy's metadata."""
        require_writable(settings, "CopyObject")
        client = _s3(ctx, destination_bucket, region)
        if not overwrite:
            _refuse_if_exists(client, destination_bucket, destination_key, "CopyObject")
        source: dict[str, str] = {"Bucket": source_bucket, "Key": source_key}
        if version_id:
            source["VersionId"] = version_id
        args: dict[str, Any] = {
            "Bucket": destination_bucket,
            "Key": destination_key,
            "CopySource": source,
        }
        if metadata is not None or content_type is not None:
            args["MetadataDirective"] = "REPLACE"
            args["Metadata"] = metadata or {}
            if content_type is not None:
                args["ContentType"] = content_type
        try:
            response = client.copy_object(**args)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "CopyObject") from exc
        result = response.get("CopyObjectResult", {})
        return clean(
            {
                "bucket": destination_bucket,
                "key": destination_key,
                "etag": result.get("ETag"),
                "last_modified": result.get("LastModified"),
                "version_id": response.get("VersionId"),
            }
        )

    @server.tool(name="object_delete", annotations=DESTRUCTIVE)
    def object_delete(
        bucket: str,
        key: str,
        version_id: str | None = None,
        confirm: bool = False,
        region: str | None = None,
    ) -> dict[str, Any]:
        """Delete an object, or one version of it with version_id. On a versioned bucket
        without version_id this adds a delete marker and the result says so. Needs
        confirm=true."""
        require_confirmed(settings, "DeleteObject", confirm)
        args = {"Bucket": bucket, "Key": key}
        if version_id:
            args["VersionId"] = version_id
        try:
            response = _s3(ctx, bucket, region).delete_object(**args)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "DeleteObject") from exc
        marker = bool(response.get("DeleteMarker"))
        result: dict[str, Any] = {
            "bucket": bucket,
            "key": key,
            "deleted": True,
            "version_id": response.get("VersionId"),
            "delete_marker": marker,
        }
        if marker and not version_id:
            result["note"] = (
                "The bucket is versioned, so a delete marker was created; earlier versions "
                "remain. Pass version_id to delete a version permanently."
            )
        return clean(result)

    @server.tool(name="object_delete_many", annotations=DESTRUCTIVE)
    def object_delete_many(
        bucket: str,
        keys: list[str] | None = None,
        prefix: str | None = None,
        max_keys: int = DELETE_BATCH,
        confirm: bool = False,
        region: str | None = None,
    ) -> dict[str, Any]:
        """Delete up to 1,000 listed keys, or up to max_keys (at most 10,000) keys under a
        prefix. Returns the deleted keys and any per-key errors, and says when keys
        remain under the prefix. Needs confirm=true."""
        require_confirmed(settings, "DeleteObjects", confirm)
        if (keys is None) == (not prefix):
            raise ToolRefused("DeleteObjects refused: pass either keys or a non-empty prefix.")
        if keys is not None and not 1 <= len(keys) <= DELETE_BATCH:
            raise ToolRefused(
                f"DeleteObjects refused: keys must list 1 to {DELETE_BATCH} keys, got {len(keys)}."
            )
        if not 1 <= max_keys <= MAX_DELETE_BY_PREFIX:
            raise ToolRefused(
                f"max_keys must be between 1 and {MAX_DELETE_BY_PREFIX}, got {max_keys}."
            )
        client = _s3(ctx, bucket, region)
        remaining = False
        if keys is None:
            keys, remaining = _keys_under(client, bucket, prefix or "", max_keys)
        result: dict[str, Any] = {**_delete_batches(client, bucket, keys), "remaining": remaining}
        if remaining:
            result["note"] = (
                f"Stopped at max_keys={max_keys}; more keys remain under {prefix!r}. "
                "Call again to continue."
            )
        return result

    @server.tool(name="object_tagging_set", annotations=WRITE)
    def object_tagging_set(
        bucket: str,
        key: str,
        tags: dict[str, str],
        version_id: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        """Replace an object's tag set with the given key-value tags."""
        require_writable(settings, "PutObjectTagging")
        args: dict[str, Any] = {
            "Bucket": bucket,
            "Key": key,
            "Tagging": {"TagSet": [{"Key": k, "Value": v} for k, v in tags.items()]},
        }
        if version_id:
            args["VersionId"] = version_id
        try:
            _s3(ctx, bucket, region).put_object_tagging(**args)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "PutObjectTagging") from exc
        return {"bucket": bucket, "key": key, "tags": tags}

    @server.tool(name="object_tagging_delete", annotations=DESTRUCTIVE)
    def object_tagging_delete(
        bucket: str,
        key: str,
        version_id: str | None = None,
        confirm: bool = False,
        region: str | None = None,
    ) -> dict[str, Any]:
        """Remove every tag from an object. Needs confirm=true."""
        require_confirmed(settings, "DeleteObjectTagging", confirm)
        args = {"Bucket": bucket, "Key": key}
        if version_id:
            args["VersionId"] = version_id
        try:
            _s3(ctx, bucket, region).delete_object_tagging(**args)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "DeleteObjectTagging") from exc
        return {"bucket": bucket, "key": key, "deleted": True}

    @server.tool(name="object_metadata_update", annotations=WRITE)
    def object_metadata_update(
        bucket: str,
        key: str,
        metadata: dict[str, str] | None = None,
        content_type: str | None = None,
        cache_control: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        """Change an object's content type, cache control or user metadata without
        re-uploading it (a copy onto itself). Fields not passed keep their current
        values; metadata, when passed, replaces all user metadata."""
        require_writable(settings, "CopyObject")
        if metadata is None and content_type is None and cache_control is None:
            raise ToolRefused("Pass at least one of metadata, content_type or cache_control.")
        client = _s3(ctx, bucket, region)
        try:
            current = client.head_object(Bucket=bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "HeadObject") from exc
        args: dict[str, Any] = {
            "Bucket": bucket,
            "Key": key,
            "CopySource": {"Bucket": bucket, "Key": key},
            "MetadataDirective": "REPLACE",
            "Metadata": metadata if metadata is not None else current.get("Metadata", {}),
        }
        new_type = content_type or current.get("ContentType")
        new_cache = cache_control or current.get("CacheControl")
        if new_type:
            args["ContentType"] = new_type
        if new_cache:
            args["CacheControl"] = new_cache
        try:
            client.copy_object(**args)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "CopyObject") from exc
        return clean(
            {
                "bucket": bucket,
                "key": key,
                "metadata": args["Metadata"],
                "content_type": new_type,
                "cache_control": new_cache,
            }
        )

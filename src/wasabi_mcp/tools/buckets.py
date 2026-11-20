"""Bucket tools: list, inspect, create, delete, rename, versioning, tagging and location.

Bucket writes must go to the bucket's own region endpoint (PRD 5.3), so this module keeps
a private ``_bucket_client`` that looks the region up with GetBucketLocation and caches it.
Each module that changes buckets has its own copy, so no shared file is edited (PRD 8.7).
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from mcp.server.mcpserver import MCPServer

from wasabi_mcp import rest
from wasabi_mcp.errors import explain
from wasabi_mcp.safety import ToolRefused, require_confirmed, require_writable
from wasabi_mcp.tools import DESTRUCTIVE, READ, WRITE, ToolContext, clean

DEFAULT_REGION = "us-east-1"
MAX_ITEMS = 1000

# Fixes appended to Wasabi's error when the next step is obvious.
HINTS = {
    "TooManyBuckets": "Wasabi allows 1,000 buckets per account; delete unused buckets first.",
    "BucketNotEmpty": "Delete the objects first, or call again with force=true.",
    "BucketAlreadyExists": "Bucket names are global across Wasabi; choose another name.",
    "BucketAlreadyOwnedByYou": "You already own this bucket.",
}


def _fail(exc: Exception, action: str) -> ToolRefused:
    refused = explain(exc, action)
    code = exc.response.get("Error", {}).get("Code") if isinstance(exc, ClientError) else None
    if code in HINTS:
        text = str(refused).rstrip()
        separator = " " if text.endswith((".", "!", "?")) else ". "
        return ToolRefused(f"{text}{separator}{HINTS[code]}")
    return refused


def _location(response: dict[str, Any]) -> str:
    """GetBucketLocation's region; an empty or missing constraint means us-east-1."""
    return response.get("LocationConstraint") or DEFAULT_REGION


def _page_size(max_items: int) -> int:
    if not 1 <= max_items <= MAX_ITEMS:
        raise ToolRefused(f"max_items must be between 1 and {MAX_ITEMS}, not {max_items}")
    return max_items


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings
    regions: dict[str, str] = {}

    def lookup_region(bucket: str) -> str:
        if bucket not in regions:
            try:
                regions[bucket] = _location(ctx.clients.s3().get_bucket_location(Bucket=bucket))
            except (ClientError, BotoCoreError) as exc:
                raise explain(exc, "GetBucketLocation") from exc
        return regions[bucket]

    def _bucket_client(bucket: str, region: str | None) -> tuple[Any, str]:
        """The S3 client for ``bucket``'s own region, and that region."""
        name = region or lookup_region(bucket)
        return ctx.clients.s3(name), name

    @server.tool(name="bucket_list", annotations=READ)
    def bucket_list(
        include_region: bool = False,
        max_items: int = 100,
        continuation_token: str | None = None,
    ) -> dict[str, Any]:
        """List buckets by name with their creation dates. With include_region=true, also
        each bucket's region (one GetBucketLocation per bucket listed)."""
        size = _page_size(max_items)
        try:
            buckets = ctx.clients.s3().list_buckets().get("Buckets", [])
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "ListBuckets") from exc
        ordered = sorted(buckets, key=lambda b: b["Name"])
        after = [b for b in ordered if continuation_token is None or b["Name"] > continuation_token]
        page, truncated = after[:size], len(after) > size
        items = [{"name": b["Name"], "creation_date": clean(b.get("CreationDate"))} for b in page]
        if include_region:
            items = [{**item, "region": lookup_region(item["name"])} for item in items]
        return {
            "items": items,
            "next_token": page[-1]["Name"] if truncated else None,
            "truncated": truncated,
        }

    @server.tool(name="bucket_get", annotations=READ)
    def bucket_get(bucket: str) -> dict[str, Any]:
        """Summarise a bucket: its region, versioning status, whether Object Lock is enabled
        and its default retention rule if any."""
        try:
            ctx.clients.s3().head_bucket(Bucket=bucket)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchBucket"):
                raise ToolRefused(
                    f"HeadBucket failed: NoSuchBucket: The bucket {bucket} does not exist."
                ) from exc
            raise explain(exc, "HeadBucket") from exc
        except BotoCoreError as exc:
            raise explain(exc, "HeadBucket") from exc
        client, region = _bucket_client(bucket, None)
        try:
            versioning = client.get_bucket_versioning(Bucket=bucket)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "GetBucketVersioning") from exc
        try:
            lock = client.get_object_lock_configuration(Bucket=bucket).get(
                "ObjectLockConfiguration", {}
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ObjectLockConfigurationNotFoundError":
                raise explain(exc, "GetObjectLockConfiguration") from exc
            lock = {}
        except BotoCoreError as exc:
            raise explain(exc, "GetObjectLockConfiguration") from exc
        # A rule sets Days or Years; drop the unset one (some servers send it as 0).
        rule = lock.get("Rule", {}).get("DefaultRetention") or {}
        retention = {key: value for key, value in rule.items() if value}
        return {
            "bucket": bucket,
            "region": region,
            "versioning": versioning.get("Status", "Unversioned"),
            "mfa_delete": versioning.get("MFADelete", "Disabled"),
            "object_lock_enabled": lock.get("ObjectLockEnabled") == "Enabled",
            "default_retention": clean(retention) if retention else None,
        }

    @server.tool(name="bucket_create", annotations=WRITE)
    def bucket_create(
        bucket: str, region: str | None = None, object_lock: bool = False
    ) -> dict[str, Any]:
        """Create a bucket in region (default: the server's region). object_lock=true enables
        Object Lock, which also turns on versioning and can never be turned off."""
        require_writable(settings, "CreateBucket")
        name = region or settings.region
        params: dict[str, Any] = {"Bucket": bucket}
        if name != DEFAULT_REGION:
            params["CreateBucketConfiguration"] = {"LocationConstraint": name}
        if object_lock:
            params["ObjectLockEnabledForBucket"] = True
        try:
            ctx.clients.s3(name).create_bucket(**params)
        except (ClientError, BotoCoreError) as exc:
            raise _fail(exc, "CreateBucket") from exc
        regions[bucket] = name
        return {
            "ok": True,
            "bucket": bucket,
            "region": name,
            "object_lock": object_lock,
            "versioning": "Enabled" if object_lock else "Unversioned",
        }

    @server.tool(name="bucket_delete", annotations=DESTRUCTIVE)
    def bucket_delete(
        bucket: str, force: bool = False, region: str | None = None, confirm: bool = False
    ) -> dict[str, Any]:
        """Delete a bucket. force=true first deletes every object in it (Wasabi
        force_delete); Compliance and Object Lock retention still apply. Needs confirm=true."""
        require_confirmed(settings, "DeleteBucket", confirm)
        client, name = _bucket_client(bucket, region)
        if force:
            rest.signed_s3(
                settings,
                "DELETE",
                bucket,
                query={"force_delete": "true"},
                region=name,
                action="DeleteBucket",
            )
        else:
            try:
                client.delete_bucket(Bucket=bucket)
            except (ClientError, BotoCoreError) as exc:
                raise _fail(exc, "DeleteBucket") from exc
        regions.pop(bucket, None)
        return {"ok": True, "bucket": bucket, "region": name, "forced": force}

    @server.tool(name="bucket_rename", annotations=DESTRUCTIVE)
    def bucket_rename(
        bucket: str, destination: str, region: str | None = None, confirm: bool = False
    ) -> dict[str, Any]:
        """Rename a bucket to destination with Wasabi's MOVE extension. Needs the
        s3:CreateBucket permission and confirm=true."""
        require_confirmed(settings, "RenameBucket", confirm)
        _, name = _bucket_client(bucket, region)
        rest.signed_s3(
            settings,
            "MOVE",
            bucket,
            headers={"Destination": destination},
            region=name,
            action="RenameBucket",
        )
        regions.pop(bucket, None)
        regions[destination] = name
        return {"ok": True, "bucket": destination, "previous_name": bucket, "region": name}

    @server.tool(name="bucket_versioning_get", annotations=READ)
    def bucket_versioning_get(bucket: str, region: str | None = None) -> dict[str, Any]:
        """Show a bucket's versioning status (Enabled, Suspended or Unversioned) and whether
        MFA Delete is on."""
        try:
            response = ctx.clients.s3(region).get_bucket_versioning(Bucket=bucket)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "GetBucketVersioning") from exc
        return {
            "bucket": bucket,
            "status": response.get("Status", "Unversioned"),
            "mfa_delete": response.get("MFADelete", "Disabled"),
        }

    @server.tool(name="bucket_versioning_set", annotations=WRITE)
    def bucket_versioning_set(
        bucket: str, enabled: bool, region: str | None = None
    ) -> dict[str, Any]:
        """Enable (enabled=true) or suspend (enabled=false) versioning on a bucket. A bucket
        that has been versioned can be suspended but never returns to unversioned."""
        require_writable(settings, "PutBucketVersioning")
        client, _ = _bucket_client(bucket, region)
        status = "Enabled" if enabled else "Suspended"
        try:
            client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": status})
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "PutBucketVersioning") from exc
        return {"ok": True, "bucket": bucket, "status": status}

    @server.tool(name="bucket_tagging_get", annotations=READ)
    def bucket_tagging_get(bucket: str, region: str | None = None) -> dict[str, Any]:
        """Show a bucket's tags as a name-to-value map, or {"configured": false} when it
        has none."""
        try:
            response = ctx.clients.s3(region).get_bucket_tagging(Bucket=bucket)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "NoSuchTagSet":
                return {"configured": False}
            raise explain(exc, "GetBucketTagging") from exc
        except BotoCoreError as exc:
            raise explain(exc, "GetBucketTagging") from exc
        # Wasabi answers an untagged bucket with an empty TagSet where AWS raises NoSuchTagSet.
        tags = {tag["Key"]: tag["Value"] for tag in response.get("TagSet") or []}
        if not tags:
            return {"configured": False}
        return {"configured": True, "bucket": bucket, "tags": tags}

    @server.tool(name="bucket_tagging_set", annotations=WRITE)
    def bucket_tagging_set(
        bucket: str, tags: dict[str, str], region: str | None = None
    ) -> dict[str, Any]:
        """Replace a bucket's whole tag set with tags (a name-to-value map). Returns the tags
        now set."""
        require_writable(settings, "PutBucketTagging")
        client, _ = _bucket_client(bucket, region)
        tag_set = [{"Key": key, "Value": value} for key, value in tags.items()]
        try:
            client.put_bucket_tagging(Bucket=bucket, Tagging={"TagSet": tag_set})
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "PutBucketTagging") from exc
        return {"ok": True, "bucket": bucket, "tags": dict(tags)}

    @server.tool(name="bucket_tagging_delete", annotations=DESTRUCTIVE)
    def bucket_tagging_delete(
        bucket: str, region: str | None = None, confirm: bool = False
    ) -> dict[str, Any]:
        """Remove every tag from a bucket. Needs confirm=true."""
        require_confirmed(settings, "DeleteBucketTagging", confirm)
        client, _ = _bucket_client(bucket, region)
        try:
            client.delete_bucket_tagging(Bucket=bucket)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "DeleteBucketTagging") from exc
        return {"ok": True, "bucket": bucket}

    @server.tool(name="bucket_location_get", annotations=READ)
    def bucket_location_get(bucket: str) -> dict[str, Any]:
        """Show the region a bucket lives in and the S3 service URL to use for it."""
        try:
            response = ctx.clients.s3().get_bucket_location(Bucket=bucket)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "GetBucketLocation") from exc
        region = _location(response)
        regions[bucket] = region
        return {"bucket": bucket, "region": region, "endpoint": settings.s3_endpoint_for(region)}

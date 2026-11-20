"""Bucket replication: configuration (boto3) and backfill of existing objects (Wasabi's
``?manifestCreateAndProcess`` extension).

Changes go to the bucket's own region (PRD 5.3) through a private ``_bucket_region`` that
looks the region up with GetBucketLocation and caches it. Each module that changes buckets
has its own copy, so no shared file is edited (PRD 8.7).
"""

from __future__ import annotations

import json
from typing import Any
from xml.etree import ElementTree

from botocore.exceptions import BotoCoreError, ClientError
from mcp.server.mcpserver import MCPServer

from wasabi_mcp import rest
from wasabi_mcp.errors import explain
from wasabi_mcp.safety import ToolRefused, require_confirmed, require_writable
from wasabi_mcp.tools import DESTRUCTIVE, READ, WRITE, ToolContext, clean

DEFAULT_REGION = "us-east-1"
BUCKET_ARN = "arn:aws:s3:::"
RULE_FIELDS = frozenset(
    {"id", "prefix", "tags", "priority", "enabled", "delete_marker_replication"}
)


def bucket_arn(name: str) -> str:
    return name if name.startswith(BUCKET_ARN) else BUCKET_ARN + name


def _filter(prefix: str | None, tags: dict[str, str] | None) -> dict[str, Any]:
    tag_list = [{"Key": key, "Value": value} for key, value in (tags or {}).items()]
    if (prefix and tag_list) or len(tag_list) > 1:
        conditions: dict[str, Any] = {"Tags": tag_list}
        if prefix:
            conditions["Prefix"] = prefix
        return {"And": conditions}
    if tag_list:
        return {"Tag": tag_list[0]}
    return {"Prefix": prefix or ""}


def replication_rules(
    rules: list[dict[str, Any]] | None,
    destination: dict[str, Any],
    delete_marker_replication: bool,
) -> list[dict[str, Any]]:
    """Validate snake_case rules and turn them into S3 ``ReplicationRule`` form.

    No rules means one rule replicating the whole bucket.
    """
    converted = []
    for number, rule in enumerate(rules or [{}], start=1):
        unknown = set(rule) - RULE_FIELDS
        if unknown:
            raise ToolRefused(
                f"Replication rule {number} has unknown fields {sorted(unknown)}; "
                f"use {', '.join(sorted(RULE_FIELDS))}"
            )
        markers = rule.get("delete_marker_replication", delete_marker_replication)
        converted.append(
            {
                "ID": rule.get("id") or f"rule-{number}",
                "Priority": rule.get("priority", number),
                "Status": "Enabled" if rule.get("enabled", True) else "Disabled",
                "Filter": _filter(rule.get("prefix"), rule.get("tags")),
                "DeleteMarkerReplication": {"Status": "Enabled" if markers else "Disabled"},
                "Destination": destination,
            }
        )
    return converted


def _job_id(content: bytes) -> str | None:
    """The job ID in a backfill response, whether Wasabi answers in JSON or XML."""
    if not content.strip():
        return None
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = None
    if isinstance(payload, dict):
        found = {key.lower().replace("_", ""): value for key, value in payload.items()}
        value = found.get("jobid", found.get("id"))
        return str(value) if value is not None else None
    try:
        root = rest.parse_xml(content)
    except ElementTree.ParseError:
        return None
    texts = {element.tag.lower(): (element.text or "").strip() for element in root.iter()}
    return texts.get("jobid") or texts.get("id") or None


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings
    regions: dict[str, str] = {}

    def _bucket_region(bucket: str, region: str | None) -> str:
        """``bucket``'s own region (``region`` when given, else looked up and cached)."""
        if region:
            return region
        if bucket not in regions:
            try:
                response = ctx.clients.s3().get_bucket_location(Bucket=bucket)
            except (ClientError, BotoCoreError) as exc:
                raise explain(exc, "GetBucketLocation") from exc
            regions[bucket] = response.get("LocationConstraint") or DEFAULT_REGION
        return regions[bucket]

    def _versioning(bucket: str, region: str) -> str:
        try:
            response = ctx.clients.s3(region).get_bucket_versioning(Bucket=bucket)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "GetBucketVersioning") from exc
        return response.get("Status", "Unversioned")

    @server.tool(name="bucket_replication_get", annotations=READ)
    def bucket_replication_get(bucket: str, region: str | None = None) -> dict[str, Any]:
        """Show a bucket's replication role and rules, or {"configured": false} when it has
        no replication configuration."""
        try:
            response = ctx.clients.s3(region).get_bucket_replication(Bucket=bucket)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ReplicationConfigurationNotFoundError":
                return {"configured": False}
            raise explain(exc, "GetBucketReplication") from exc
        except BotoCoreError as exc:
            raise explain(exc, "GetBucketReplication") from exc
        configuration = response.get("ReplicationConfiguration", {})
        return {
            "configured": True,
            "bucket": bucket,
            "role": configuration.get("Role", ""),
            "rules": clean(configuration.get("Rules", [])),
        }

    @server.tool(name="bucket_replication_set", annotations=DESTRUCTIVE)
    def bucket_replication_set(
        bucket: str,
        destination_bucket: str,
        rules: list[dict[str, Any]] | None = None,
        delete_marker_replication: bool = False,
        role: str = "",
        destination_account: str | None = None,
        owner_translation: bool = False,
        region: str | None = None,
        destination_region: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Replace a bucket's replication configuration, replicating new objects to
        destination_bucket. rules ({id, prefix, tags, priority, enabled,
        delete_marker_replication}) default to the whole bucket. Same account: leave role
        empty; cross-account: give role (ARN), destination_account and optionally
        owner_translation. Both buckets must have the same versioning state; existing
        objects need bucket_replication_backfill. Needs confirm=true."""
        require_confirmed(settings, "PutBucketReplication", confirm)
        if owner_translation and not destination_account:
            raise ToolRefused("owner_translation needs destination_account")
        if destination_account and not role:
            raise ToolRefused("Cross-account replication needs role (an IAM role ARN)")
        destination: dict[str, Any] = {"Bucket": bucket_arn(destination_bucket)}
        if destination_account:
            destination["Account"] = destination_account
        if owner_translation:
            destination["AccessControlTranslation"] = {"Owner": "Destination"}
        converted = replication_rules(rules, destination, delete_marker_replication)
        source_region = _bucket_region(bucket, region)
        target = destination_bucket.removeprefix(BUCKET_ARN)
        source_state = _versioning(bucket, source_region)
        target_state = _versioning(target, _bucket_region(target, destination_region))
        if source_state != target_state:
            raise ToolRefused(
                f"PutBucketReplication refused: {bucket} versioning is {source_state} but "
                f"{target} is {target_state}. Both buckets must have the same versioning state; "
                "enable versioning on both with bucket_versioning_set first."
            )
        try:
            ctx.clients.s3(source_region).put_bucket_replication(
                Bucket=bucket, ReplicationConfiguration={"Role": role, "Rules": converted}
            )
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "PutBucketReplication") from exc
        return {"ok": True, "bucket": bucket, "role": role, "rules": converted}

    @server.tool(name="bucket_replication_delete", annotations=DESTRUCTIVE)
    def bucket_replication_delete(
        bucket: str, region: str | None = None, confirm: bool = False
    ) -> dict[str, Any]:
        """Delete a bucket's replication configuration; objects already replicated stay.
        Needs confirm=true."""
        require_confirmed(settings, "DeleteBucketReplication", confirm)
        client = ctx.clients.s3(_bucket_region(bucket, region))
        try:
            client.delete_bucket_replication(Bucket=bucket)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "DeleteBucketReplication") from exc
        return {"ok": True, "bucket": bucket}

    @server.tool(name="bucket_replication_backfill", annotations=WRITE)
    def bucket_replication_backfill(bucket: str, region: str | None = None) -> dict[str, Any]:
        """Start a Wasabi job that replicates a bucket's existing objects under its current
        replication rules (replication alone only copies new objects). Returns the job ID."""
        require_writable(settings, "ReplicationBackfill")
        response = rest.signed_s3(
            settings,
            "PUT",
            bucket,
            query={"manifestCreateAndProcess": None},
            region=_bucket_region(bucket, region),
            action="ReplicationBackfill",
        )
        return {"ok": True, "bucket": bucket, "job_id": _job_id(response.content)}

"""Bucket logging (boto3) and inventory configurations (Wasabi's ``?inventory`` API).

Changes go to the bucket's own region (PRD 5.3) through a private ``_bucket_region`` that
looks the region up with GetBucketLocation and caches it. Each module that changes buckets
has its own copy, so no shared file is edited (PRD 8.7).
"""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree

from botocore.exceptions import BotoCoreError, ClientError
from mcp.server.mcpserver import MCPServer

from wasabi_mcp import rest
from wasabi_mcp.errors import explain
from wasabi_mcp.safety import ToolRefused, require_confirmed, require_writable
from wasabi_mcp.tools import DESTRUCTIVE, READ, WRITE, ToolContext

DEFAULT_REGION = "us-east-1"
S3_NAMESPACE = "http://s3.amazonaws.com/doc/2006-03-01/"
BUCKET_ARN = "arn:aws:s3:::"
FREQUENCIES = ("Daily", "Weekly")
VERSIONS = ("Current", "All")
# The optional inventory fields Wasabi documents.
OPTIONAL_FIELDS = (
    "Size",
    "LastModifiedDate",
    "ETag",
    "StorageClass",
    "IsMultipartUploaded",
    "ReplicationStatus",
    "EncryptionStatus",
    "ObjectLockRetainUntilDate",
    "ObjectLockMode",
    "ObjectLockLegalHoldStatus",
    "ChecksumAlgorithm",
    "ObjectAccessControlList",
    "ObjectOwner",
)
HINTS = {
    "LoggingBucketSameOwner": "The target bucket must belong to the same Wasabi account.",
    "InvalidTargetBucketForLogging": (
        "The target bucket must exist in this account and allow log delivery."
    ),
}


def _fail(exc: Exception, action: str) -> ToolRefused:
    refused = explain(exc, action)
    code = exc.response.get("Error", {}).get("Code") if isinstance(exc, ClientError) else None
    if code in HINTS:
        text = str(refused).rstrip()
        separator = " " if text.endswith((".", "!", "?")) else ". "
        return ToolRefused(f"{text}{separator}{HINTS[code]}")
    return refused


def _choice(name: str, value: str, choices: tuple[str, ...]) -> str:
    for choice in choices:
        if value.lower() == choice.lower():
            return choice
    raise ToolRefused(f"{name} must be one of {', '.join(choices)}, not {value!r}")


def inventory_xml(
    inventory_id: str,
    destination_bucket: str,
    *,
    destination_prefix: str | None,
    destination_account_id: str | None,
    frequency: str,
    included_versions: str,
    prefix: str | None,
    optional_fields: list[str] | None,
    enabled: bool,
    output_format: str,
) -> bytes:
    """A validated ``InventoryConfiguration`` document (CSV only)."""
    if not inventory_id.strip():
        raise ToolRefused("inventory_id must not be empty")
    if output_format.upper() != "CSV":
        raise ToolRefused(f"Wasabi inventory supports the CSV format only, not {output_format!r}")
    fields = [_choice("optional field", field, OPTIONAL_FIELDS) for field in optional_fields or []]
    root = ElementTree.Element("InventoryConfiguration", xmlns=S3_NAMESPACE)

    def add(parent: ElementTree.Element, tag: str, text: str | None = None) -> Any:
        child = ElementTree.SubElement(parent, tag)
        if text is not None:
            child.text = text
        return child

    add(root, "Id", inventory_id)
    add(root, "IsEnabled", "true" if enabled else "false")
    if prefix:
        add(add(root, "Filter"), "Prefix", prefix)
    target = add(add(root, "Destination"), "S3BucketDestination")
    add(target, "Format", "CSV")
    if destination_account_id:
        add(target, "AccountId", destination_account_id)
    bucket_arn = destination_bucket
    if not bucket_arn.startswith(BUCKET_ARN):
        bucket_arn = BUCKET_ARN + bucket_arn
    add(target, "Bucket", bucket_arn)
    if destination_prefix:
        add(target, "Prefix", destination_prefix)
    add(add(root, "Schedule"), "Frequency", _choice("frequency", frequency, FREQUENCIES))
    add(root, "IncludedObjectVersions", _choice("included_versions", included_versions, VERSIONS))
    if fields:
        container = add(root, "OptionalFields")
        for field in fields:
            add(container, "Field", field)
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def inventory_dict(element: ElementTree.Element) -> dict[str, Any]:
    """An ``InventoryConfiguration`` element (namespaces stripped) as plain JSON."""
    target = element.find("Destination/S3BucketDestination")

    def text(parent: ElementTree.Element | None, path: str) -> str | None:
        return parent.findtext(path) if parent is not None else None

    bucket = text(target, "Bucket") or ""
    return {
        "id": element.findtext("Id"),
        "enabled": (element.findtext("IsEnabled") or "").lower() == "true",
        "prefix": element.findtext("Filter/Prefix"),
        "destination_bucket": bucket.removeprefix(BUCKET_ARN) or None,
        "destination_prefix": text(target, "Prefix"),
        "destination_account_id": text(target, "AccountId"),
        "format": text(target, "Format"),
        "frequency": element.findtext("Schedule/Frequency"),
        "included_versions": element.findtext("IncludedObjectVersions"),
        "optional_fields": [field.text for field in element.findall("OptionalFields/Field")],
    }


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

    @server.tool(name="bucket_logging_get", annotations=READ)
    def bucket_logging_get(bucket: str, region: str | None = None) -> dict[str, Any]:
        """Show whether access logging is on for a bucket, and its target bucket and
        prefix."""
        try:
            response = ctx.clients.s3(region).get_bucket_logging(Bucket=bucket)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "GetBucketLogging") from exc
        logging = response.get("LoggingEnabled")
        return {
            "bucket": bucket,
            "enabled": logging is not None,
            "target_bucket": logging.get("TargetBucket") if logging else None,
            "target_prefix": logging.get("TargetPrefix") if logging else None,
        }

    @server.tool(name="bucket_logging_set", annotations=WRITE)
    def bucket_logging_set(
        bucket: str,
        target_bucket: str | None = None,
        target_prefix: str = "",
        enabled: bool = True,
        region: str | None = None,
    ) -> dict[str, Any]:
        """Turn on access logging for a bucket into target_bucket (same account) under
        target_prefix, or turn it off with enabled=false. Wasabi delivers logs best effort,
        with no SLA."""
        require_writable(settings, "PutBucketLogging")
        if enabled and not target_bucket:
            raise ToolRefused("target_bucket is required when enabled=true")
        status: dict[str, Any] = {}
        if enabled:
            status["LoggingEnabled"] = {
                "TargetBucket": target_bucket,
                "TargetPrefix": target_prefix,
            }
        client = ctx.clients.s3(_bucket_region(bucket, region))
        try:
            client.put_bucket_logging(Bucket=bucket, BucketLoggingStatus=status)
        except (ClientError, BotoCoreError) as exc:
            raise _fail(exc, "PutBucketLogging") from exc
        return {
            "ok": True,
            "bucket": bucket,
            "enabled": enabled,
            "target_bucket": target_bucket if enabled else None,
            "target_prefix": target_prefix if enabled else None,
        }

    @server.tool(name="bucket_inventory_list", annotations=READ)
    def bucket_inventory_list(
        bucket: str, continuation_token: str | None = None, region: str | None = None
    ) -> dict[str, Any]:
        """List a bucket's inventory configurations (Wasabi returns up to 100 per page;
        pass next_token back as continuation_token for more)."""
        query: dict[str, str | None] = {"inventory": None}
        if continuation_token:
            query["continuation-token"] = continuation_token
        response = rest.signed_s3(
            settings,
            "GET",
            bucket,
            query=query,
            region=region,
            action="ListBucketInventoryConfigurations",
        )
        root = rest.parse_xml(response.content)
        items = [inventory_dict(element) for element in root.findall("InventoryConfiguration")]
        truncated = (root.findtext("IsTruncated") or "").lower() == "true"
        return {
            "items": items,
            "next_token": root.findtext("NextContinuationToken") if truncated else None,
            "truncated": truncated,
        }

    @server.tool(name="bucket_inventory_get", annotations=READ)
    def bucket_inventory_get(
        bucket: str, inventory_id: str, region: str | None = None
    ) -> dict[str, Any]:
        """Show one inventory configuration of a bucket, or {"configured": false} when no
        configuration has that ID."""
        try:
            response = rest.signed_s3(
                settings,
                "GET",
                bucket,
                query={"inventory": None, "id": inventory_id},
                region=region,
                action="GetBucketInventoryConfiguration",
            )
        except ToolRefused as exc:
            if ": NoSuchConfiguration:" in str(exc):
                return {"configured": False}
            raise
        return {"configured": True, **inventory_dict(rest.parse_xml(response.content))}

    @server.tool(name="bucket_inventory_set", annotations=WRITE)
    def bucket_inventory_set(
        bucket: str,
        inventory_id: str,
        destination_bucket: str,
        destination_prefix: str | None = None,
        destination_account_id: str | None = None,
        frequency: str = "Daily",
        included_versions: str = "Current",
        prefix: str | None = None,
        optional_fields: list[str] | None = None,
        enabled: bool = True,
        output_format: str = "CSV",
        region: str | None = None,
    ) -> dict[str, Any]:
        """Create or replace an inventory configuration: a CSV report, Daily or Weekly, of
        Current or All versions under an optional prefix, written to destination_bucket.
        optional_fields come from Wasabi's list (Size, LastModifiedDate, ETag, StorageClass,
        IsMultipartUploaded, ReplicationStatus, EncryptionStatus, ObjectLockRetainUntilDate,
        ObjectLockMode, ObjectLockLegalHoldStatus, ChecksumAlgorithm, ObjectAccessControlList,
        ObjectOwner)."""
        require_writable(settings, "PutBucketInventoryConfiguration")
        body = inventory_xml(
            inventory_id,
            destination_bucket,
            destination_prefix=destination_prefix,
            destination_account_id=destination_account_id,
            frequency=frequency,
            included_versions=included_versions,
            prefix=prefix,
            optional_fields=optional_fields,
            enabled=enabled,
            output_format=output_format,
        )
        rest.signed_s3(
            settings,
            "PUT",
            bucket,
            query={"inventory": None, "id": inventory_id},
            headers={"Content-Type": "application/xml"},
            body=body,
            region=_bucket_region(bucket, region),
            action="PutBucketInventoryConfiguration",
        )
        return {"ok": True, "bucket": bucket, **inventory_dict(rest.parse_xml(body))}

    @server.tool(name="bucket_inventory_delete", annotations=DESTRUCTIVE)
    def bucket_inventory_delete(
        bucket: str, inventory_id: str, region: str | None = None, confirm: bool = False
    ) -> dict[str, Any]:
        """Delete one inventory configuration of a bucket; reports already written stay.
        Needs confirm=true."""
        require_confirmed(settings, "DeleteBucketInventoryConfiguration", confirm)
        rest.signed_s3(
            settings,
            "DELETE",
            bucket,
            query={"inventory": None, "id": inventory_id},
            region=_bucket_region(bucket, region),
            action="DeleteBucketInventoryConfiguration",
        )
        return {"ok": True, "bucket": bucket, "inventory_id": inventory_id}

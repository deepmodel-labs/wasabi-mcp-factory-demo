"""CORS and lifecycle rules on buckets.

Changes go to the bucket's own region (PRD 5.3) through a private ``_bucket_client`` that
looks the region up with GetBucketLocation and caches it. Each module that changes buckets
has its own copy, so no shared file is edited (PRD 8.7).
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from mcp.server.mcpserver import MCPServer

from wasabi_mcp.errors import explain
from wasabi_mcp.safety import ToolRefused, require_confirmed, require_writable
from wasabi_mcp.tools import DESTRUCTIVE, READ, WRITE, ToolContext, clean

DEFAULT_REGION = "us-east-1"
CORS_METHODS = ("GET", "PUT", "POST", "DELETE", "HEAD")
# snake_case rule field -> S3 CORSRule field.
CORS_FIELDS = {
    "id": "ID",
    "allowed_origins": "AllowedOrigins",
    "allowed_methods": "AllowedMethods",
    "allowed_headers": "AllowedHeaders",
    "expose_headers": "ExposeHeaders",
    "max_age_seconds": "MaxAgeSeconds",
}
LIFECYCLE_FIELDS = frozenset(
    {
        "ID",
        "Status",
        "Filter",
        "Prefix",
        "Expiration",
        "NoncurrentVersionExpiration",
        "AbortIncompleteMultipartUpload",
    }
)
LIFECYCLE_ACTIONS = ("Expiration", "NoncurrentVersionExpiration", "AbortIncompleteMultipartUpload")
TRANSITION_FIELDS = frozenset({"Transitions", "Transition", "NoncurrentVersionTransitions"})
FILTER_FIELDS = frozenset({"Prefix", "Tag", "And", "ObjectSizeGreaterThan", "ObjectSizeLessThan"})


def _code(exc: ClientError) -> str | None:
    return exc.response.get("Error", {}).get("Code")


def cors_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate snake_case CORS rules and turn them into S3 ``CORSRule`` form."""
    if not rules:
        raise ToolRefused("rules must hold at least one CORS rule")
    converted = []
    for number, rule in enumerate(rules, start=1):
        unknown = set(rule) - set(CORS_FIELDS)
        if unknown:
            raise ToolRefused(
                f"CORS rule {number} has unknown fields {sorted(unknown)}; "
                f"use {', '.join(CORS_FIELDS)}"
            )
        if not rule.get("allowed_origins") or not rule.get("allowed_methods"):
            raise ToolRefused(f"CORS rule {number} needs allowed_origins and allowed_methods")
        methods = [method.upper() for method in rule["allowed_methods"]]
        bad = [method for method in methods if method not in CORS_METHODS]
        if bad:
            raise ToolRefused(
                f"CORS rule {number} has unsupported methods {bad}; use {', '.join(CORS_METHODS)}"
            )
        converted.append(
            {
                CORS_FIELDS[name]: value
                for name, value in {**rule, "allowed_methods": methods}.items()
            }
        )
    return converted


def lifecycle_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate S3-shaped lifecycle rules, rejecting transitions (Wasabi has one storage
    class). Rules default to ``Status: Enabled`` and an empty-prefix filter."""
    if not rules:
        raise ToolRefused("rules must hold at least one lifecycle rule")
    converted = []
    for number, rule in enumerate(rules, start=1):
        transitions = set(rule) & TRANSITION_FIELDS
        if transitions:
            raise ToolRefused(
                f"Lifecycle rule {number} uses {sorted(transitions)}: Wasabi has a single "
                "storage class, so transitions aren't supported. Use Expiration instead."
            )
        unknown = set(rule) - LIFECYCLE_FIELDS
        if unknown:
            raise ToolRefused(
                f"Lifecycle rule {number} has unknown fields {sorted(unknown)}; "
                f"use {', '.join(sorted(LIFECYCLE_FIELDS))}"
            )
        if not set(rule) & set(LIFECYCLE_ACTIONS):
            raise ToolRefused(
                f"Lifecycle rule {number} needs an action: {', '.join(LIFECYCLE_ACTIONS)}"
            )
        status = rule.get("Status", "Enabled")
        if status not in ("Enabled", "Disabled"):
            raise ToolRefused(f"Lifecycle rule {number} Status must be Enabled or Disabled")
        unknown_filter = set(rule.get("Filter") or {}) - FILTER_FIELDS
        if unknown_filter:
            raise ToolRefused(
                f"Lifecycle rule {number} Filter has unknown fields {sorted(unknown_filter)}; "
                f"use {', '.join(sorted(FILTER_FIELDS))}"
            )
        result = {**rule, "Status": status}
        if "Filter" not in rule and "Prefix" not in rule:
            result["Filter"] = {"Prefix": ""}
        converted.append(result)
    return converted


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings
    regions: dict[str, str] = {}

    def _bucket_client(bucket: str, region: str | None) -> Any:
        """The S3 client for ``bucket``'s own region."""
        if region:
            return ctx.clients.s3(region)
        if bucket not in regions:
            try:
                response = ctx.clients.s3().get_bucket_location(Bucket=bucket)
            except (ClientError, BotoCoreError) as exc:
                raise explain(exc, "GetBucketLocation") from exc
            regions[bucket] = response.get("LocationConstraint") or DEFAULT_REGION
        return ctx.clients.s3(regions[bucket])

    @server.tool(name="bucket_cors_get", annotations=READ)
    def bucket_cors_get(bucket: str, region: str | None = None) -> dict[str, Any]:
        """Show a bucket's CORS rules, or {"configured": false} when it has none; Wasabi
        then answers browsers with permissive default CORS headers."""
        try:
            response = ctx.clients.s3(region).get_bucket_cors(Bucket=bucket)
        except ClientError as exc:
            if _code(exc) == "NoSuchCORSConfiguration":
                return {"configured": False}
            raise explain(exc, "GetBucketCors") from exc
        except BotoCoreError as exc:
            raise explain(exc, "GetBucketCors") from exc
        return {"configured": True, "bucket": bucket, "rules": clean(response["CORSRules"])}

    @server.tool(name="bucket_cors_set", annotations=WRITE)
    def bucket_cors_set(
        bucket: str, rules: list[dict[str, Any]], region: str | None = None
    ) -> dict[str, Any]:
        """Replace a bucket's CORS rules. Each rule takes allowed_origins and allowed_methods
        (GET, PUT, POST, DELETE, HEAD), and optionally allowed_headers, expose_headers,
        max_age_seconds and id. Returns the rules set."""
        require_writable(settings, "PutBucketCors")
        converted = cors_rules(rules)
        try:
            _bucket_client(bucket, region).put_bucket_cors(
                Bucket=bucket, CORSConfiguration={"CORSRules": converted}
            )
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "PutBucketCors") from exc
        return {"ok": True, "bucket": bucket, "rules": converted}

    @server.tool(name="bucket_cors_delete", annotations=DESTRUCTIVE)
    def bucket_cors_delete(
        bucket: str, region: str | None = None, confirm: bool = False
    ) -> dict[str, Any]:
        """Delete a bucket's CORS rules, restoring Wasabi's default CORS behaviour. Needs
        confirm=true."""
        require_confirmed(settings, "DeleteBucketCors", confirm)
        try:
            _bucket_client(bucket, region).delete_bucket_cors(Bucket=bucket)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "DeleteBucketCors") from exc
        return {"ok": True, "bucket": bucket}

    @server.tool(name="bucket_lifecycle_get", annotations=READ)
    def bucket_lifecycle_get(bucket: str, region: str | None = None) -> dict[str, Any]:
        """Show a bucket's lifecycle rules, or {"configured": false} when it has none."""
        try:
            response = ctx.clients.s3(region).get_bucket_lifecycle_configuration(Bucket=bucket)
        except ClientError as exc:
            if _code(exc) == "NoSuchLifecycleConfiguration":
                return {"configured": False}
            raise explain(exc, "GetBucketLifecycleConfiguration") from exc
        except BotoCoreError as exc:
            raise explain(exc, "GetBucketLifecycleConfiguration") from exc
        return {"configured": True, "bucket": bucket, "rules": clean(response["Rules"])}

    @server.tool(name="bucket_lifecycle_set", annotations=DESTRUCTIVE)
    def bucket_lifecycle_set(
        bucket: str,
        rules: list[dict[str, Any]],
        region: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Replace all of a bucket's lifecycle rules with rules in S3 form (ID, Status,
        Filter with Prefix/Tag/And/ObjectSize*, and Expiration, NoncurrentVersionExpiration
        or AbortIncompleteMultipartUpload); transitions are rejected because Wasabi has one
        storage class. Rules run daily at 00:00 UTC and take 24-48 hours to apply, can't
        delete locked objects, and can't be set while MFA Delete is on. Needs confirm=true."""
        require_confirmed(settings, "PutBucketLifecycleConfiguration", confirm)
        converted = lifecycle_rules(rules)
        try:
            _bucket_client(bucket, region).put_bucket_lifecycle_configuration(
                Bucket=bucket, LifecycleConfiguration={"Rules": converted}
            )
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "PutBucketLifecycleConfiguration") from exc
        return {"ok": True, "bucket": bucket, "rules": clean(converted)}

    @server.tool(name="bucket_lifecycle_delete", annotations=DESTRUCTIVE)
    def bucket_lifecycle_delete(
        bucket: str, region: str | None = None, confirm: bool = False
    ) -> dict[str, Any]:
        """Delete all of a bucket's lifecycle rules. Needs confirm=true."""
        require_confirmed(settings, "DeleteBucketLifecycle", confirm)
        try:
            _bucket_client(bucket, region).delete_bucket_lifecycle(Bucket=bucket)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "DeleteBucketLifecycle") from exc
        return {"ok": True, "bucket": bucket}

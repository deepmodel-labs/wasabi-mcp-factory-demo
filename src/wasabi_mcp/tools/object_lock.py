"""Object Lock: a bucket's default retention, and per-object retention and legal hold.

Changes go to the bucket's own region (PRD 5.3) through a private ``_bucket_client`` that
looks the region up with GetBucketLocation and caches it. Each module that changes buckets
has its own copy, so no shared file is edited (PRD 8.7).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from mcp.server.mcpserver import MCPServer

from wasabi_mcp.errors import explain
from wasabi_mcp.safety import ToolRefused, require_confirmed
from wasabi_mcp.tools import DESTRUCTIVE, READ, ToolContext, clean

DEFAULT_REGION = "us-east-1"
MODES = ("COMPLIANCE", "GOVERNANCE")
# Codes Wasabi and S3 use when a bucket or object has no lock configuration.
NOT_CONFIGURED = frozenset(
    {"ObjectLockConfigurationNotFoundError", "NoSuchObjectLockConfiguration"}
)


def _code(exc: ClientError) -> str | None:
    return exc.response.get("Error", {}).get("Code")


def _mode(mode: str) -> str:
    upper = mode.upper()
    if upper not in MODES:
        raise ToolRefused(f"mode must be COMPLIANCE or GOVERNANCE, not {mode!r}")
    return upper


def lock_configuration(
    mode: str | None, days: int | None, years: int | None, clear_default: bool
) -> dict[str, Any]:
    """The ObjectLockConfiguration to send, validated: a default rule with ``mode`` and
    exactly one of ``days`` or ``years``, or ``Enabled`` only when ``clear_default``."""
    if clear_default:
        if mode is not None or days is not None or years is not None:
            raise ToolRefused("clear_default=true takes no mode, days or years")
        return {"ObjectLockEnabled": "Enabled"}
    if mode is None:
        raise ToolRefused("Give mode (COMPLIANCE or GOVERNANCE), or clear_default=true")
    if (days is None) == (years is None):
        raise ToolRefused("Give exactly one of days or years")
    period = {"Days": days} if days is not None else {"Years": years}
    if next(iter(period.values())) < 1:
        raise ToolRefused("days or years must be at least 1")
    return {
        "ObjectLockEnabled": "Enabled",
        "Rule": {"DefaultRetention": {"Mode": _mode(mode), **period}},
    }


def parse_retain_until(retain_until: str) -> datetime:
    """An ISO 8601 date or time, in UTC when no offset is given."""
    try:
        parsed = datetime.fromisoformat(retain_until)
    except ValueError as exc:
        raise ToolRefused(f"retain_until must be ISO 8601, not {retain_until!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _default_retention(lock: dict[str, Any]) -> dict[str, Any] | None:
    rule = lock.get("Rule", {}).get("DefaultRetention") or {}
    # A rule sets Days or Years; drop the unset one (some servers send it as 0).
    retention = {key: value for key, value in rule.items() if value}
    return retention or None


def _object_params(bucket: str, key: str, version_id: str | None) -> dict[str, str]:
    params = {"Bucket": bucket, "Key": key}
    if version_id:
        params["VersionId"] = version_id
    return params


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

    @server.tool(name="bucket_object_lock_get", annotations=READ)
    def bucket_object_lock_get(bucket: str, region: str | None = None) -> dict[str, Any]:
        """Show whether Object Lock is enabled on a bucket and its default retention rule
        (mode, and days or years), if any."""
        try:
            response = ctx.clients.s3(region).get_object_lock_configuration(Bucket=bucket)
        except ClientError as exc:
            if _code(exc) in NOT_CONFIGURED:
                return {"bucket": bucket, "enabled": False, "default_retention": None}
            raise explain(exc, "GetObjectLockConfiguration") from exc
        except BotoCoreError as exc:
            raise explain(exc, "GetObjectLockConfiguration") from exc
        lock = response.get("ObjectLockConfiguration", {})
        return {
            "bucket": bucket,
            "enabled": lock.get("ObjectLockEnabled") == "Enabled",
            "default_retention": _default_retention(lock),
        }

    @server.tool(name="bucket_object_lock_set", annotations=DESTRUCTIVE)
    def bucket_object_lock_set(
        bucket: str,
        mode: str | None = None,
        days: int | None = None,
        years: int | None = None,
        clear_default: bool = False,
        region: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Set a bucket's default retention rule: mode COMPLIANCE or GOVERNANCE and either
        days or years; clear_default=true removes the rule instead. Object Lock itself can
        only be enabled when the bucket is created (bucket_create object_lock=true). Needs
        confirm=true."""
        require_confirmed(settings, "PutObjectLockConfiguration", confirm)
        configuration = lock_configuration(mode, days, years, clear_default)
        try:
            _bucket_client(bucket, region).put_object_lock_configuration(
                Bucket=bucket, ObjectLockConfiguration=configuration
            )
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "PutObjectLockConfiguration") from exc
        return {
            "ok": True,
            "bucket": bucket,
            "enabled": True,
            "default_retention": _default_retention(configuration),
        }

    @server.tool(name="object_retention_get", annotations=READ)
    def object_retention_get(
        bucket: str, key: str, version_id: str | None = None, region: str | None = None
    ) -> dict[str, Any]:
        """Show an object's (or one version's) retention mode and retain-until date, or
        {"configured": false} when it has none."""
        try:
            response = ctx.clients.s3(region).get_object_retention(
                **_object_params(bucket, key, version_id)
            )
        except ClientError as exc:
            if _code(exc) in NOT_CONFIGURED:
                return {"configured": False}
            raise explain(exc, "GetObjectRetention") from exc
        except BotoCoreError as exc:
            raise explain(exc, "GetObjectRetention") from exc
        retention = response.get("Retention", {})
        return {
            "configured": True,
            "bucket": bucket,
            "key": key,
            "version_id": version_id,
            "mode": retention.get("Mode"),
            "retain_until": clean(retention.get("RetainUntilDate")),
        }

    @server.tool(name="object_retention_set", annotations=DESTRUCTIVE)
    def object_retention_set(
        bucket: str,
        key: str,
        mode: str,
        retain_until: str,
        version_id: str | None = None,
        bypass_governance: bool = False,
        region: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Set an object's (or one version's) retention: mode COMPLIANCE or GOVERNANCE until
        retain_until (ISO 8601, UTC if no offset). Compliance retention can't be shortened
        or removed by anyone; bypass_governance=true lets a permitted user shorten
        governance retention. Needs confirm=true."""
        require_confirmed(settings, "PutObjectRetention", confirm)
        retention = {"Mode": _mode(mode), "RetainUntilDate": parse_retain_until(retain_until)}
        params: dict[str, Any] = {**_object_params(bucket, key, version_id)}
        if bypass_governance:
            params["BypassGovernanceRetention"] = True
        try:
            _bucket_client(bucket, region).put_object_retention(**params, Retention=retention)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "PutObjectRetention") from exc
        return {
            "ok": True,
            "bucket": bucket,
            "key": key,
            "version_id": version_id,
            "mode": retention["Mode"],
            "retain_until": clean(retention["RetainUntilDate"]),
        }

    @server.tool(name="object_legal_hold_get", annotations=READ)
    def object_legal_hold_get(
        bucket: str, key: str, version_id: str | None = None, region: str | None = None
    ) -> dict[str, Any]:
        """Show whether an object (or one version) is under legal hold."""
        try:
            response = ctx.clients.s3(region).get_object_legal_hold(
                **_object_params(bucket, key, version_id)
            )
        except ClientError as exc:
            if _code(exc) in NOT_CONFIGURED:
                return {"bucket": bucket, "key": key, "version_id": version_id, "on": False}
            raise explain(exc, "GetObjectLegalHold") from exc
        except BotoCoreError as exc:
            raise explain(exc, "GetObjectLegalHold") from exc
        status = response.get("LegalHold", {}).get("Status")
        return {"bucket": bucket, "key": key, "version_id": version_id, "on": status == "ON"}

    @server.tool(name="object_legal_hold_set", annotations=DESTRUCTIVE)
    def object_legal_hold_set(
        bucket: str,
        key: str,
        on: bool,
        version_id: str | None = None,
        region: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Place (on=true) or release (on=false) a legal hold on an object or one version.
        A held object can't be deleted until the hold is released. Needs confirm=true."""
        require_confirmed(settings, "PutObjectLegalHold", confirm)
        try:
            _bucket_client(bucket, region).put_object_legal_hold(
                **_object_params(bucket, key, version_id),
                LegalHold={"Status": "ON" if on else "OFF"},
            )
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "PutObjectLegalHold") from exc
        return {"ok": True, "bucket": bucket, "key": key, "version_id": version_id, "on": on}

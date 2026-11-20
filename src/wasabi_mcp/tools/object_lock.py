"""Object Lock and retention (PRD section 6.8): object-level retention and legal hold."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from botocore.exceptions import BotoCoreError, ClientError
from mcp.server.mcpserver import MCPServer

from wasabi_mcp.errors import explain
from wasabi_mcp.safety import ToolRefused, require_confirmed
from wasabi_mcp.tools import DESTRUCTIVE, READ, ToolContext, clean
from wasabi_mcp.tools.objects_read import _s3

# Codes S3-compatible services send when an object or bucket has no lock settings.
NOT_CONFIGURED_CODES = {
    "NoSuchObjectLockConfiguration",
    "ObjectLockConfigurationNotFoundError",
    "NoSuchObjectLegalHold",
}
COMPLIANCE_NOTE = (
    "COMPLIANCE mode retention can't be shortened or removed by anyone, including the "
    "account owner, until it expires."
)


def _not_configured(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") in NOT_CONFIGURED_CODES


def _object_args(bucket: str, key: str, version_id: str | None) -> dict[str, Any]:
    args = {"Bucket": bucket, "Key": key}
    if version_id:
        args["VersionId"] = version_id
    return args


def _parse_until(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ToolRefused(f"retain_until must be an ISO 8601 date or time, got {value!r}.") from exc
    return parsed if parsed.tzinfo else parsed.astimezone()


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings

    @server.tool(name="object_retention_get", annotations=READ)
    def object_retention_get(
        bucket: str,
        key: str,
        version_id: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        """Show an object's Object Lock retention (mode and retain-until time), or
        configured: false when it has none."""
        try:
            response = _s3(ctx, bucket, region).get_object_retention(
                **_object_args(bucket, key, version_id)
            )
        except ClientError as exc:
            if _not_configured(exc):
                return {"configured": False}
            raise explain(exc, "GetObjectRetention") from exc
        except BotoCoreError as exc:
            raise explain(exc, "GetObjectRetention") from exc
        retention = response.get("Retention") or {}
        if not retention.get("Mode"):
            return {"configured": False}
        return clean(
            {
                "configured": True,
                "mode": retention["Mode"],
                "retain_until": retention.get("RetainUntilDate"),
            }
        )

    @server.tool(name="object_retention_set", annotations=DESTRUCTIVE)
    def object_retention_set(
        bucket: str,
        key: str,
        mode: Literal["GOVERNANCE", "COMPLIANCE"],
        retain_until: str,
        version_id: str | None = None,
        bypass_governance: bool = False,
        confirm: bool = False,
        region: str | None = None,
    ) -> dict[str, Any]:
        """Set an object's retention: mode GOVERNANCE or COMPLIANCE until retain_until
        (ISO 8601). bypass_governance lets you shorten GOVERNANCE retention; COMPLIANCE
        retention can't be shortened by anyone. Needs confirm=true."""
        require_confirmed(settings, "PutObjectRetention", confirm)
        until = _parse_until(retain_until)
        args: dict[str, Any] = {
            **_object_args(bucket, key, version_id),
            "Retention": {"Mode": mode, "RetainUntilDate": until},
        }
        if bypass_governance:
            args["BypassGovernanceRetention"] = True
        try:
            _s3(ctx, bucket, region).put_object_retention(**args)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "PutObjectRetention") from exc
        result: dict[str, Any] = {
            "bucket": bucket,
            "key": key,
            "mode": mode,
            "retain_until": until,
        }
        if mode == "COMPLIANCE":
            result["note"] = COMPLIANCE_NOTE
        return clean(result)

    @server.tool(name="object_legal_hold_get", annotations=READ)
    def object_legal_hold_get(
        bucket: str,
        key: str,
        version_id: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        """Show whether an object has a legal hold (ON or OFF), or configured: false when
        none was ever set."""
        try:
            response = _s3(ctx, bucket, region).get_object_legal_hold(
                **_object_args(bucket, key, version_id)
            )
        except ClientError as exc:
            if _not_configured(exc):
                return {"configured": False}
            raise explain(exc, "GetObjectLegalHold") from exc
        except BotoCoreError as exc:
            raise explain(exc, "GetObjectLegalHold") from exc
        status = (response.get("LegalHold") or {}).get("Status")
        if not status:
            return {"configured": False}
        return {"configured": True, "status": status, "on": status == "ON"}

    @server.tool(name="object_legal_hold_set", annotations=DESTRUCTIVE)
    def object_legal_hold_set(
        bucket: str,
        key: str,
        on: bool,
        version_id: str | None = None,
        confirm: bool = False,
        region: str | None = None,
    ) -> dict[str, Any]:
        """Turn an object's legal hold on or off. While on, the object version can't be
        deleted or overwritten, whatever its retention. Needs confirm=true."""
        require_confirmed(settings, "PutObjectLegalHold", confirm)
        status = "ON" if on else "OFF"
        try:
            _s3(ctx, bucket, region).put_object_legal_hold(
                **_object_args(bucket, key, version_id), LegalHold={"Status": status}
            )
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "PutObjectLegalHold") from exc
        return {"bucket": bucket, "key": key, "status": status, "on": on}

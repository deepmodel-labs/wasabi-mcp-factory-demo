"""Wasabi Compliance: bucket compliance policy and per-object compliance settings.

These are Wasabi extensions (``?compliance``), so every call goes through ``rest.signed_s3``.
Writes go to the bucket's own region endpoint (PRD 5.3), so this module keeps a private
region lookup with its own cache, as the other bucket-changing modules do (PRD 8.7).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from xml.etree import ElementTree

from botocore.exceptions import BotoCoreError, ClientError
from mcp.server.mcpserver import MCPServer

from wasabi_mcp import rest
from wasabi_mcp.errors import explain
from wasabi_mcp.safety import ToolRefused, require_confirmed
from wasabi_mcp.tools import DESTRUCTIVE, READ, ToolContext, clean

DEFAULT_REGION = "us-east-1"
LOCK_WORDS = ("now", "off")
LOCKED_HINT = "Locked compliance settings can't be reduced or turned off; contact Wasabi Support."


def _location(response: dict[str, Any]) -> str:
    """GetBucketLocation's region; an empty or missing constraint means us-east-1."""
    return response.get("LocationConstraint") or DEFAULT_REGION


def _snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).lower()


def _value(text: str | None) -> Any:
    """XML text as JSON: booleans and integers converted, other text unchanged."""
    if text is None:
        return None
    text = text.strip()
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    if text.isdigit():
        return int(text)
    return text


def _fields(content: bytes) -> dict[str, Any]:
    """The children of a compliance configuration document as snake_case JSON."""
    if not content.strip():
        return {}
    root = rest.parse_xml(content)
    return {
        # A hex digest can be all digits, so it stays text.
        _snake(child.tag): (child.text or "").strip()
        if child.tag == "SHA256"
        else _value(child.text)
        for child in root
    }


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _parse_time(value: str, field: str, action: str) -> datetime:
    """An ISO 8601 date or date-time as an aware UTC datetime (naive means UTC)."""
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        raise ToolRefused(
            f"{action} refused: {field} must be an ISO 8601 date or time "
            f"(for example 2030-01-31T00:00:00Z), not {value!r}"
        ) from None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _lock_time(value: str) -> str:
    word = value.strip().lower()
    if word in LOCK_WORDS:
        return word
    return _iso(_parse_time(value, "lock_time", "PutBucketCompliance"))


def _retention_days(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ToolRefused(
            f"PutBucketCompliance refused: retention_days must be a non-negative integer, "
            f"not {value!r}"
        )
    return value


def _document(tag: str, fields: dict[str, str]) -> bytes:
    root = ElementTree.Element(tag)
    for name, text in fields.items():
        ElementTree.SubElement(root, name).text = text
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def _with_hint(exc: ToolRefused) -> ToolRefused:
    text = str(exc)
    if "ComplianceSettingsLocked" in text:
        return ToolRefused(f"{text.rstrip('.')}. {LOCKED_HINT}")
    return exc


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings
    regions: dict[str, str] = {}

    def bucket_region(bucket: str, region: str | None) -> str:
        """``region`` if given, else ``bucket``'s own region (looked up once, then cached)."""
        if region:
            return region
        if bucket not in regions:
            try:
                response = ctx.clients.s3(settings.region).get_bucket_location(Bucket=bucket)
            except (ClientError, BotoCoreError) as exc:
                raise explain(exc, "GetBucketLocation") from exc
            regions[bucket] = _location(response)
        return regions[bucket]

    def get_object_compliance(bucket: str, key: str, region: str) -> dict[str, Any]:
        response = rest.signed_s3(
            settings,
            "GET",
            bucket,
            key,
            query={"compliance": None},
            region=region,
            action="GetObjectCompliance",
        )
        return _fields(response.content)

    @server.tool(name="bucket_compliance_get", annotations=READ)
    def bucket_compliance_get(bucket: str, region: str | None = None) -> dict[str, Any]:
        """Show a bucket's Wasabi compliance policy: status, lock_time, is_locked,
        retention_days, conditional_hold and delete_after_retention."""
        response = rest.signed_s3(
            settings,
            "GET",
            bucket,
            query={"compliance": None},
            region=region,
            action="GetBucketCompliance",
        )
        fields = _fields(response.content)
        return clean(
            {
                "bucket": bucket,
                "status": fields.get("status"),
                "lock_time": fields.get("lock_time"),
                "is_locked": fields.get("is_locked"),
                "retention_days": fields.get("retention_days"),
                "conditional_hold": fields.get("conditional_hold"),
                "delete_after_retention": fields.get("delete_after_retention"),
            }
        )

    @server.tool(name="bucket_compliance_set", annotations=DESTRUCTIVE)
    def bucket_compliance_set(
        bucket: str,
        enabled: bool,
        retention_days: int,
        conditional_hold: bool | None = None,
        lock_time: str | None = None,
        delete_after_retention: bool | None = None,
        region: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Set a bucket's Wasabi compliance policy, which applies at once to every object:
        retention_days is required, and lock_time is an ISO date, "now" or "off". Once locked,
        the settings can't be reduced without Wasabi Support (ComplianceSettingsLocked); needs
        confirm=true."""
        require_confirmed(settings, "PutBucketCompliance", confirm)
        fields = {
            "Status": "enabled" if enabled else "disabled",
            "RetentionDays": str(_retention_days(retention_days)),
        }
        if lock_time is not None:
            fields["LockTime"] = _lock_time(lock_time)
        if conditional_hold is not None:
            fields["ConditionalHold"] = _bool(conditional_hold)
        if delete_after_retention is not None:
            fields["DeleteAfterRetention"] = _bool(delete_after_retention)
        name = bucket_region(bucket, region)
        try:
            rest.signed_s3(
                settings,
                "PUT",
                bucket,
                query={"compliance": None},
                headers={"Content-Type": "application/xml"},
                body=_document("BucketComplianceConfiguration", fields),
                region=name,
                action="PutBucketCompliance",
            )
        except ToolRefused as exc:
            raise _with_hint(exc) from exc
        return {
            "ok": True,
            "bucket": bucket,
            "region": name,
            **{_snake(tag): _value(text) for tag, text in fields.items()},
        }

    @server.tool(name="object_compliance_get", annotations=READ)
    def object_compliance_get(bucket: str, key: str, region: str | None = None) -> dict[str, Any]:
        """Show an object's Wasabi compliance settings: retention_time, conditional_hold,
        legal_hold and the SHA256 Wasabi computed, which is null for multipart and composed
        objects."""
        fields = get_object_compliance(bucket, key, region or settings.region)
        return clean(
            {
                "bucket": bucket,
                "key": key,
                "retention_time": fields.get("retention_time"),
                "conditional_hold": fields.get("conditional_hold"),
                "legal_hold": fields.get("legal_hold"),
                "sha256": fields.get("sha256"),
            }
        )

    @server.tool(name="object_compliance_set", annotations=DESTRUCTIVE)
    def object_compliance_set(
        bucket: str,
        key: str,
        release_conditional_hold: bool = False,
        legal_hold: bool | None = None,
        retention_time: str | None = None,
        region: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Change an object's Wasabi compliance settings: release_conditional_hold=true releases
        the hold (it can't be re-applied), legal_hold sets or clears a legal hold, and
        retention_time (ISO 8601) can only extend retention. Needs confirm=true."""
        require_confirmed(settings, "PutObjectCompliance", confirm)
        if not release_conditional_hold and legal_hold is None and retention_time is None:
            raise ToolRefused(
                "PutObjectCompliance refused: give release_conditional_hold=true, legal_hold "
                "or retention_time"
            )
        new_time = (
            _parse_time(retention_time, "retention_time", "PutObjectCompliance")
            if retention_time is not None
            else None
        )
        name = bucket_region(bucket, region)
        fields: dict[str, str] = {}
        if release_conditional_hold:
            fields["ConditionalHold"] = "false"
        if legal_hold is not None:
            fields["LegalHold"] = _bool(legal_hold)
        if new_time is not None:
            current = get_object_compliance(bucket, key, name).get("retention_time")
            if isinstance(current, str) and current:
                current_time = _parse_time(current, "RetentionTime", "PutObjectCompliance")
                if new_time < current_time:
                    raise ToolRefused(
                        f"PutObjectCompliance refused: retention_time {_iso(new_time)} is "
                        f"earlier than the current retention time {_iso(current_time)}; "
                        "retention can only be extended"
                    )
            fields["RetentionTime"] = _iso(new_time)
        rest.signed_s3(
            settings,
            "PUT",
            bucket,
            key,
            query={"compliance": None},
            headers={"Content-Type": "application/xml"},
            body=_document("ObjectComplianceConfiguration", fields),
            region=name,
            action="PutObjectCompliance",
        )
        return {
            "ok": True,
            "bucket": bucket,
            "key": key,
            "region": name,
            **{_snake(tag): _value(text) for tag, text in fields.items()},
        }

"""Wasabi object extensions: rename (MOVE), rename a prefix, compose and append.

These have no boto3 equivalent, so every call goes through ``rest.signed_s3``. Writes must
go to the bucket's own region endpoint (PRD 5.3), so this module keeps a private region
lookup with its own cache, as the other bucket-changing modules do (PRD 8.7).
"""

from __future__ import annotations

import re
from typing import Any
from xml.etree import ElementTree

import httpx
from botocore.exceptions import BotoCoreError, ClientError
from mcp.server.mcpserver import MCPServer

from wasabi_mcp import rest
from wasabi_mcp.errors import explain
from wasabi_mcp.safety import ToolRefused, require_confirmed, require_writable
from wasabi_mcp.tools import DESTRUCTIVE, WRITE, ToolContext, clean

DEFAULT_REGION = "us-east-1"
MAX_COMPOSE_SOURCES = 32
MAX_APPEND_BYTES = 1024 * 1024


def _location(response: dict[str, Any]) -> str:
    """GetBucketLocation's region; an empty or missing constraint means us-east-1."""
    return response.get("LocationConstraint") or DEFAULT_REGION


def _snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()


def _element(element: ElementTree.Element) -> Any:
    """An XML element as JSON: text for a leaf, else a dict of snake_case children."""
    children = list(element)
    if not children:
        return (element.text or "").strip()
    return {_snake(child.tag): _element(child) for child in children}


def _move_result(response: httpx.Response) -> dict[str, Any]:
    """Wasabi's ``<MoveObjectResult>``: the prefixes, each key moved (or failed) and the count."""
    if not response.content.strip():
        return {"moved": [], "errors": []}
    try:
        root = rest.parse_xml(response.content)
    except ElementTree.ParseError:
        return {"moved": [], "errors": [], "raw": response.text.strip()[:2000]}
    moved: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    for child in root:
        if child.tag == "MoveObject":
            entry = _element(child)
            (errors if isinstance(entry, dict) and "error" in entry else moved).append(entry)
        else:
            summary[_snake(child.tag)] = _element(child)
    if "move_count" in summary:
        try:
            summary["move_count"] = int(summary["move_count"])
        except ValueError:
            pass
    return {**summary, "moved": moved, "errors": errors}


def _compose_body(sources: list[str]) -> bytes:
    root = ElementTree.Element("ComposeRequest")
    for source in sources:
        component = ElementTree.SubElement(root, "Component")
        ElementTree.SubElement(component, "Key").text = source
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def _validate_sources(sources: list[str]) -> list[str]:
    if not 1 <= len(sources) <= MAX_COMPOSE_SOURCES:
        raise ToolRefused(
            f"ComposeObject refused: give 1 to {MAX_COMPOSE_SOURCES} source keys, "
            f"not {len(sources)}"
        )
    if any(not isinstance(source, str) or not source for source in sources):
        raise ToolRefused("ComposeObject refused: every source key must be a non-empty string")
    return sources


def _etag(response: httpx.Response) -> str | None:
    etag = response.headers.get("ETag")
    return etag.strip('"') if etag else None


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

    @server.tool(name="object_rename", annotations=DESTRUCTIVE)
    def object_rename(
        bucket: str,
        key: str,
        destination: str,
        overwrite: bool = False,
        region: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Rename an object (all its versions) to the destination key in the same bucket with
        Wasabi's MOVE extension. An existing destination is only replaced with overwrite=true;
        needs s3:PutObject and confirm=true."""
        require_confirmed(settings, "RenameObject", confirm)
        name = bucket_region(bucket, region)
        response = rest.signed_s3(
            settings,
            "MOVE",
            bucket,
            key,
            headers={"Destination": destination, "Overwrite": str(overwrite).lower()},
            region=name,
            action="RenameObject",
        )
        result = _move_result(response)
        if result["errors"]:
            raise ToolRefused(f"RenameObject failed: {clean(result['errors'])}")
        return {
            "ok": True,
            "bucket": bucket,
            "key": destination,
            "previous_key": key,
            "overwrite": overwrite,
            "region": name,
        }

    @server.tool(name="object_rename_prefix", annotations=DESTRUCTIVE)
    def object_rename_prefix(
        bucket: str,
        prefix: str,
        destination: str,
        overwrite: bool = False,
        quiet: bool = False,
        region: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Rename every key (all versions) under prefix so it starts with destination instead,
        with Wasabi's MOVE extension. Returns Wasabi's per-key results, with keys that failed
        (for example because overwrite=false and the target exists) under errors; quiet=true
        lists only the failures. Needs confirm=true."""
        require_confirmed(settings, "RenameObjectPrefix", confirm)
        if not prefix:
            raise ToolRefused("RenameObjectPrefix refused: prefix must not be empty")
        name = bucket_region(bucket, region)
        response = rest.signed_s3(
            settings,
            "MOVE",
            bucket,
            prefix,
            headers={
                "Destination": destination,
                "Overwrite": str(overwrite).lower(),
                "X-Wasabi-Prefix": "true",
                "X-Wasabi-Quiet": str(quiet).lower(),
            },
            region=name,
            action="RenameObjectPrefix",
        )
        return {
            "bucket": bucket,
            "prefix": prefix,
            "destination": destination,
            "region": name,
            **_move_result(response),
        }

    @server.tool(name="object_compose", annotations=WRITE)
    def object_compose(
        bucket: str, key: str, sources: list[str], region: str | None = None
    ) -> dict[str, Any]:
        """Create key by linking 1 to 32 source objects in the same bucket, in order, with
        Wasabi's compose extension (no data is copied). Customer-provided (SSE-C) keys aren't
        supported, and the ETag is a hash of the part MD5s, not the MD5 of the whole object."""
        require_writable(settings, "ComposeObject")
        body = _compose_body(_validate_sources(sources))
        name = bucket_region(bucket, region)
        response = rest.signed_s3(
            settings,
            "PUT",
            bucket,
            key,
            query={"compose": None},
            headers={"Content-Type": "text/xml"},
            body=body,
            region=name,
            action="ComposeObject",
        )
        return {
            "ok": True,
            "bucket": bucket,
            "key": key,
            "sources": sources,
            "etag": _etag(response),
            "version_id": response.headers.get("x-amz-version-id"),
            "region": name,
        }

    @server.tool(name="object_append", annotations=WRITE)
    def object_append(
        bucket: str,
        key: str,
        text: str,
        content_type: str = "text/plain",
        region: str | None = None,
    ) -> dict[str, Any]:
        """Append text (at most 1 MiB as UTF-8) to an object with Wasabi's append extension.
        If the key doesn't exist, the object is created with the text and `created` is true.
        Wasabi has no conditional create, so a concurrent writer could race with that create.
        An object can be appended to at most 1,023 times, and each append is billed as a small
        object, so append in large chunks."""
        require_writable(settings, "AppendObject")
        body = text.encode("utf-8")
        if len(body) > MAX_APPEND_BYTES:
            raise ToolRefused(
                f"AppendObject refused: text is {len(body)} bytes; the limit is "
                f"{MAX_APPEND_BYTES} bytes (1 MiB)"
            )
        name = bucket_region(bucket, region)
        headers = {"Content-Type": content_type}
        try:
            response = rest.signed_s3(
                settings,
                "PUT",
                bucket,
                key,
                query={"append": None},
                headers=headers,
                body=body,
                region=name,
                action="AppendObject",
            )
            created = False
        except ToolRefused as error:
            if error.code != "NoSuchKey":
                raise
            response = rest.signed_s3(
                settings,
                "PUT",
                bucket,
                key,
                headers=headers,
                body=body,
                region=name,
                action="PutObject",
            )
            created = True
        extra = {
            header.lower().removeprefix("x-wasabi-").replace("-", "_"): value
            for header, value in response.headers.items()
            if header.lower().startswith("x-wasabi-")
        }
        return {
            "ok": True,
            "bucket": bucket,
            "key": key,
            "appended_bytes": len(body),
            "etag": _etag(response),
            "version_id": response.headers.get("x-amz-version-id"),
            "region": name,
            "created": created,
            **extra,
        }

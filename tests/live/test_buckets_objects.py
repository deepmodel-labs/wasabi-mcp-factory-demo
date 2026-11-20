"""Live bucket, object, presigned GET and lifecycle flows (PRD section 8.4)."""

from __future__ import annotations

import pytest

from tests.helpers import call
from tests.live.support import fetch

pytestmark = pytest.mark.live


async def test_bucket_create_versioning_tagging_delete(live_server, new_name):
    name = new_name("bucket")
    created = await call(live_server, "bucket_create", bucket=name)
    assert created["bucket"] == name
    listed = await call(live_server, "bucket_list", max_items=1_000)
    assert name in [item["name"] for item in listed["items"]]

    await call(live_server, "bucket_versioning_set", bucket=name, enabled=True)
    versioning = await call(live_server, "bucket_versioning_get", bucket=name)
    assert versioning["status"] == "Enabled"

    tags = {"purpose": "wasabi-mcp-live-test"}
    await call(live_server, "bucket_tagging_set", bucket=name, tags=tags)
    assert (await call(live_server, "bucket_tagging_get", bucket=name))["tags"] == tags
    await call(live_server, "bucket_tagging_delete", bucket=name, confirm=True)
    assert (await call(live_server, "bucket_tagging_get", bucket=name)) == {"configured": False}

    deleted = await call(live_server, "bucket_delete", bucket=name, confirm=True)
    assert deleted["ok"] is True
    listed = await call(live_server, "bucket_list", max_items=1_000)
    assert name not in [item["name"] for item in listed["items"]]


async def test_object_put_get_copy_rename_delete(live_server, bucket):
    await call(live_server, "object_put_text", bucket=bucket, key="notes/a.txt", text="hello")
    read = await call(live_server, "object_get_text", bucket=bucket, key="notes/a.txt")
    assert read["text"] == "hello"

    await call(
        live_server,
        "object_copy",
        source_bucket=bucket,
        source_key="notes/a.txt",
        destination_bucket=bucket,
        destination_key="notes/b.txt",
    )
    copied = await call(live_server, "object_get_text", bucket=bucket, key="notes/b.txt")
    assert copied["text"] == "hello"

    await call(
        live_server,
        "object_rename",
        bucket=bucket,
        key="notes/b.txt",
        destination="notes/c.txt",
        confirm=True,
    )
    keys = await _keys(live_server, bucket, "notes/")
    assert keys == ["notes/a.txt", "notes/c.txt"]

    for key in keys:
        await call(live_server, "object_delete", bucket=bucket, key=key, confirm=True)
    assert await _keys(live_server, bucket, "notes/") == []


async def test_object_get_text_after_append(live_server, bucket):
    # The bucket comes from new_name, so the finalizer empties and deletes it.
    key = "append/log.txt"
    await call(live_server, "object_put_text", bucket=bucket, key=key, text="first line\n")
    await call(live_server, "object_append", bucket=bucket, key=key, text="second line\n")
    read = await call(live_server, "object_get_text", bucket=bucket, key=key)
    assert read["text"] == "first line\nsecond line\n"


async def test_object_append_creates_a_missing_key(live_server, bucket):
    # Wasabi answers ?append on a missing key with NoSuchKey; the tool creates the object.
    key = "append/new.txt"
    result = await call(live_server, "object_append", bucket=bucket, key=key, text="first line\n")
    assert result["created"] is True
    read = await call(live_server, "object_get_text", bucket=bucket, key=key)
    assert read["text"] == "first line\n"


async def test_presigned_get_downloads_over_http(live_server, bucket):
    await call(live_server, "object_put_text", bucket=bucket, key="share/file.txt", text="shared")
    result = await call(
        live_server, "presign_get", bucket=bucket, key="share/file.txt", expires_seconds=300
    )
    assert fetch(result["url"]) == b"shared"


async def test_lifecycle_set_get_delete(live_server, bucket):
    rule = {
        "ID": "expire-tmp",
        "Status": "Enabled",
        "Filter": {"Prefix": "tmp/"},
        "Expiration": {"Days": 30},
    }
    await call(live_server, "bucket_lifecycle_set", bucket=bucket, rules=[rule], confirm=True)
    got = await call(live_server, "bucket_lifecycle_get", bucket=bucket)
    assert got["configured"] is True
    assert [(r["ID"], r["Expiration"]["Days"]) for r in got["rules"]] == [("expire-tmp", 30)]

    await call(live_server, "bucket_lifecycle_delete", bucket=bucket, confirm=True)
    assert await call(live_server, "bucket_lifecycle_get", bucket=bucket) == {"configured": False}


async def _keys(server, bucket: str, prefix: str) -> list[str]:
    listed = await call(server, "object_list", bucket=bucket, prefix=prefix)
    return sorted(item["key"] for item in listed["items"])

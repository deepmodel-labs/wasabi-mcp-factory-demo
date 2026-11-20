from concurrent.futures import ThreadPoolExecutor

import boto3
import pytest

from tests.conftest import FAKE_ACCESS_KEY, FAKE_SECRET_KEY
from tests.helpers import ToolError, call

BUCKET = "writes"

WRITE_CALLS = [
    ("object_put_text", {"bucket": BUCKET, "key": "k", "text": "x"}),
    (
        "object_copy",
        {
            "source_bucket": BUCKET,
            "source_key": "k",
            "destination_bucket": BUCKET,
            "destination_key": "k2",
        },
    ),
    ("object_delete", {"bucket": BUCKET, "key": "k", "confirm": True}),
    ("object_delete_many", {"bucket": BUCKET, "keys": ["k"], "confirm": True}),
    ("object_tagging_set", {"bucket": BUCKET, "key": "k", "tags": {"a": "b"}}),
    ("object_tagging_delete", {"bucket": BUCKET, "key": "k", "confirm": True}),
    ("object_metadata_update", {"bucket": BUCKET, "key": "k", "content_type": "text/csv"}),
]

DELETE_CALLS = [
    ("object_delete", {"bucket": BUCKET, "key": "k"}),
    ("object_delete_many", {"bucket": BUCKET, "keys": ["k"]}),
    ("object_tagging_delete", {"bucket": BUCKET, "key": "k"}),
]


@pytest.fixture
def s3(moto_url):
    client = boto3.client(
        "s3",
        region_name="us-east-1",
        endpoint_url=moto_url,
        aws_access_key_id=FAKE_ACCESS_KEY,
        aws_secret_access_key=FAKE_SECRET_KEY,
    )
    client.create_bucket(Bucket=BUCKET)
    return client


def _keys(s3, bucket=BUCKET):
    paginator = s3.get_paginator("list_objects_v2")
    return [
        obj["Key"] for page in paginator.paginate(Bucket=bucket) for obj in page.get("Contents", [])
    ]


async def test_object_put_text_writes_text_with_type_and_metadata(server, s3):
    result = await call(
        server,
        "object_put_text",
        bucket=BUCKET,
        key="notes/a.md",
        text="# héllo",
        content_type="text/markdown",
        metadata={"owner": "team"},
    )
    assert result["size"] == len("# héllo".encode())
    assert result["etag"]
    stored = s3.get_object(Bucket=BUCKET, Key="notes/a.md")
    assert stored["Body"].read().decode() == "# héllo"
    assert stored["ContentType"] == "text/markdown"
    assert stored["Metadata"] == {"owner": "team"}


async def test_object_put_text_refuses_existing_key_without_overwrite(server, s3):
    s3.put_object(Bucket=BUCKET, Key="k", Body=b"old")
    with pytest.raises(ToolError, match="overwrite=true"):
        await call(server, "object_put_text", bucket=BUCKET, key="k", text="new")
    assert s3.get_object(Bucket=BUCKET, Key="k")["Body"].read() == b"old"
    await call(server, "object_put_text", bucket=BUCKET, key="k", text="new", overwrite=True)
    assert s3.get_object(Bucket=BUCKET, Key="k")["Body"].read() == b"new"


async def test_object_put_text_rejects_bodies_over_one_mib(server, s3):
    with pytest.raises(ToolError, match="1 MiB"):
        await call(server, "object_put_text", bucket=BUCKET, key="big", text="a" * 1_048_577)
    assert _keys(s3) == []


async def test_object_put_text_counts_encoded_bytes(server, s3):
    # 349,526 three-byte characters is just over 1 MiB once encoded.
    with pytest.raises(ToolError, match="1 MiB"):
        await call(server, "object_put_text", bucket=BUCKET, key="big", text="€" * 349_526)


async def test_object_copy_within_and_across_buckets(server, s3):
    s3.create_bucket(Bucket="other")
    s3.put_object(Bucket=BUCKET, Key="src", Body=b"data", Metadata={"a": "1"})
    same = await call(
        server,
        "object_copy",
        source_bucket=BUCKET,
        source_key="src",
        destination_bucket=BUCKET,
        destination_key="dst",
    )
    assert same["etag"]
    assert s3.get_object(Bucket=BUCKET, Key="dst")["Body"].read() == b"data"
    await call(
        server,
        "object_copy",
        source_bucket=BUCKET,
        source_key="src",
        destination_bucket="other",
        destination_key="dst",
        metadata={"b": "2"},
    )
    assert s3.head_object(Bucket="other", Key="dst")["Metadata"] == {"b": "2"}


async def test_object_copy_refuses_existing_destination_without_overwrite(server, s3):
    s3.put_object(Bucket=BUCKET, Key="src", Body=b"new")
    s3.put_object(Bucket=BUCKET, Key="dst", Body=b"old")
    args = {
        "source_bucket": BUCKET,
        "source_key": "src",
        "destination_bucket": BUCKET,
        "destination_key": "dst",
    }
    with pytest.raises(ToolError, match="already exists"):
        await call(server, "object_copy", **args)
    await call(server, "object_copy", overwrite=True, **args)
    assert s3.get_object(Bucket=BUCKET, Key="dst")["Body"].read() == b"new"


async def test_object_copy_of_a_source_version(server, s3):
    s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
    first = s3.put_object(Bucket=BUCKET, Key="src", Body=b"v1")["VersionId"]
    s3.put_object(Bucket=BUCKET, Key="src", Body=b"v2")
    await call(
        server,
        "object_copy",
        source_bucket=BUCKET,
        source_key="src",
        destination_bucket=BUCKET,
        destination_key="old",
        version_id=first,
    )
    assert s3.get_object(Bucket=BUCKET, Key="old")["Body"].read() == b"v1"


async def test_object_copy_missing_source_is_explained(server, s3):
    with pytest.raises(ToolError, match="CopyObject failed"):
        await call(
            server,
            "object_copy",
            source_bucket=BUCKET,
            source_key="missing",
            destination_bucket=BUCKET,
            destination_key="dst",
        )


@pytest.mark.parametrize(("name", "arguments"), WRITE_CALLS, ids=[c[0] for c in WRITE_CALLS])
async def test_every_write_tool_refuses_in_read_only_mode(read_only_server, s3, name, arguments):
    s3.put_object(Bucket=BUCKET, Key="k", Body=b"keep")
    with pytest.raises(ToolError, match="read-only"):
        await call(read_only_server, name, **arguments)
    assert _keys(s3) == ["k"]
    assert s3.get_object_tagging(Bucket=BUCKET, Key="k")["TagSet"] == []


@pytest.mark.parametrize(("name", "arguments"), DELETE_CALLS, ids=[c[0] for c in DELETE_CALLS])
async def test_delete_tools_refuse_without_confirm(server, s3, name, arguments):
    s3.put_object(Bucket=BUCKET, Key="k", Body=b"keep", Tagging="a=b")
    with pytest.raises(ToolError, match="confirm=true"):
        await call(server, name, **arguments)
    assert _keys(s3) == ["k"]
    assert s3.get_object_tagging(Bucket=BUCKET, Key="k")["TagSet"] == [{"Key": "a", "Value": "b"}]


async def test_object_delete_removes_the_object(server, s3):
    s3.put_object(Bucket=BUCKET, Key="k", Body=b"x")
    result = await call(server, "object_delete", bucket=BUCKET, key="k", confirm=True)
    assert result["deleted"] is True
    assert result["delete_marker"] is False
    assert _keys(s3) == []


async def test_object_delete_on_versioned_bucket_reports_delete_marker(server, s3):
    s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
    version = s3.put_object(Bucket=BUCKET, Key="k", Body=b"x")["VersionId"]
    result = await call(server, "object_delete", bucket=BUCKET, key="k", confirm=True)
    assert result["delete_marker"] is True
    assert "delete marker" in result["note"]
    permanent = await call(
        server, "object_delete", bucket=BUCKET, key="k", version_id=version, confirm=True
    )
    assert permanent["version_id"] == version
    assert "note" not in permanent


async def test_object_delete_many_by_keys(server, s3):
    for key in ("a", "b", "c"):
        s3.put_object(Bucket=BUCKET, Key=key, Body=b"x")
    result = await call(server, "object_delete_many", bucket=BUCKET, keys=["a", "b"], confirm=True)
    assert sorted(result["deleted"]) == ["a", "b"]
    assert result["errors"] == []
    assert result["remaining"] is False
    assert _keys(s3) == ["c"]


async def test_object_delete_many_by_prefix_across_more_than_a_thousand_objects(server, s3):
    keys = [f"tmp/{index:05d}" for index in range(1_105)]
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda key: s3.put_object(Bucket=BUCKET, Key=key, Body=b"x"), keys))
    s3.put_object(Bucket=BUCKET, Key="keep/me", Body=b"x")
    partial = await call(
        server, "object_delete_many", bucket=BUCKET, prefix="tmp/", max_keys=1_050, confirm=True
    )
    assert len(partial["deleted"]) == 1_050
    assert partial["remaining"] is True
    assert "max_keys" in partial["note"]
    rest = await call(server, "object_delete_many", bucket=BUCKET, prefix="tmp/", confirm=True)
    assert len(rest["deleted"]) == 55
    assert rest["remaining"] is False
    assert _keys(s3) == ["keep/me"]


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"prefix": ""},
        {"keys": ["a"], "prefix": "p/"},
        {"keys": []},
        {"keys": [f"k{i}" for i in range(1_001)]},
        {"prefix": "p/", "max_keys": 10_001},
    ],
    ids=["neither", "empty-prefix", "both", "no-keys", "too-many-keys", "max-keys"],
)
async def test_object_delete_many_rejects_bad_selections(server, s3, arguments):
    with pytest.raises(ToolError):
        await call(server, "object_delete_many", bucket=BUCKET, confirm=True, **arguments)


async def test_object_tagging_set_and_delete(server, s3):
    s3.put_object(Bucket=BUCKET, Key="k", Body=b"x", Tagging="old=1")
    result = await call(server, "object_tagging_set", bucket=BUCKET, key="k", tags={"env": "prod"})
    assert result["tags"] == {"env": "prod"}
    assert s3.get_object_tagging(Bucket=BUCKET, Key="k")["TagSet"] == [
        {"Key": "env", "Value": "prod"}
    ]
    await call(server, "object_tagging_delete", bucket=BUCKET, key="k", confirm=True)
    assert s3.get_object_tagging(Bucket=BUCKET, Key="k")["TagSet"] == []


async def test_object_metadata_update_replaces_selected_fields(server, s3):
    s3.put_object(
        Bucket=BUCKET,
        Key="k",
        Body=b"body",
        ContentType="text/plain",
        Metadata={"owner": "a"},
    )
    result = await call(
        server, "object_metadata_update", bucket=BUCKET, key="k", cache_control="max-age=60"
    )
    assert result["content_type"] == "text/plain"
    head = s3.head_object(Bucket=BUCKET, Key="k")
    assert head["CacheControl"] == "max-age=60"
    assert head["ContentType"] == "text/plain"
    assert head["Metadata"] == {"owner": "a"}

    await call(
        server,
        "object_metadata_update",
        bucket=BUCKET,
        key="k",
        metadata={"owner": "b"},
        content_type="text/csv",
    )
    head = s3.head_object(Bucket=BUCKET, Key="k")
    assert head["Metadata"] == {"owner": "b"}
    assert head["ContentType"] == "text/csv"
    assert s3.get_object(Bucket=BUCKET, Key="k")["Body"].read() == b"body"


async def test_object_metadata_update_needs_a_field(server, s3):
    with pytest.raises(ToolError, match="at least one"):
        await call(server, "object_metadata_update", bucket=BUCKET, key="k")

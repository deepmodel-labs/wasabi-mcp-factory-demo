import io
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import boto3
import httpx
import pytest
import respx
from botocore.awsrequest import AWSResponse
from botocore.exceptions import FlexibleChecksumError

from tests.conftest import FAKE_ACCESS_KEY, FAKE_SECRET_KEY
from tests.helpers import ToolError, call
from wasabi_mcp.clients import Clients
from wasabi_mcp.tools.objects_read import _REGIONS, _size_human

BUCKET = "reads"


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


def test_size_human_uses_decimal_units():
    assert _size_human(0) == "0 B"
    assert _size_human(999) == "999 B"
    assert _size_human(1_200_000) == "1.2 MB"
    assert _size_human(None) is None


async def test_object_list_returns_items_and_common_prefixes(server, s3):
    s3.put_object(Bucket=BUCKET, Key="a.txt", Body=b"hello")
    s3.put_object(Bucket=BUCKET, Key="dir/b.txt", Body=b"x")
    result = await call(server, "object_list", bucket=BUCKET, delimiter="/")
    assert [item["key"] for item in result["items"]] == ["a.txt"]
    assert result["items"][0]["size"] == 5
    assert result["items"][0]["size_human"] == "5 B"
    assert result["items"][0]["etag"]
    assert result["common_prefixes"] == ["dir/"]
    assert result["truncated"] is False
    assert result["next_token"] is None


async def test_object_list_paginates_with_next_token(server, s3):
    for index in range(5):
        s3.put_object(Bucket=BUCKET, Key=f"k{index}", Body=b"x")
    first = await call(server, "object_list", bucket=BUCKET, max_items=3)
    assert len(first["items"]) == 3
    assert first["truncated"] is True
    second = await call(
        server, "object_list", bucket=BUCKET, max_items=3, continuation_token=first["next_token"]
    )
    keys = [item["key"] for item in first["items"] + second["items"]]
    assert keys == [f"k{index}" for index in range(5)]
    assert second["truncated"] is False


async def test_object_list_refuses_max_items_out_of_range(server, s3):
    with pytest.raises(ToolError, match="max_items"):
        await call(server, "object_list", bucket=BUCKET, max_items=1001)


async def test_object_list_missing_bucket_is_explained(server, moto_url):
    with pytest.raises(ToolError, match="NoSuchBucket"):
        await call(server, "object_list", bucket="no-such-bucket")


async def test_object_version_list_includes_delete_markers_and_paginates(server, s3):
    s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
    s3.put_object(Bucket=BUCKET, Key="v.txt", Body=b"one")
    s3.put_object(Bucket=BUCKET, Key="v.txt", Body=b"two")
    s3.delete_object(Bucket=BUCKET, Key="v.txt")
    s3.put_object(Bucket=BUCKET, Key="w.txt", Body=b"three")
    everything = await call(server, "object_version_list", bucket=BUCKET)
    assert len(everything["items"]) == 4
    assert sum(item["delete_marker"] for item in everything["items"]) == 1
    assert all(item["version_id"] for item in everything["items"])

    seen = []
    token = None
    for _ in range(10):
        page = await call(
            server, "object_version_list", bucket=BUCKET, max_items=1, continuation_token=token
        )
        seen.extend(page["items"])
        token = page["next_token"]
        if not page["truncated"]:
            break
    assert sorted(item["version_id"] for item in seen) == sorted(
        item["version_id"] for item in everything["items"]
    )


async def test_object_version_list_rejects_foreign_token(server, s3):
    with pytest.raises(ToolError, match="continuation_token"):
        await call(server, "object_version_list", bucket=BUCKET, continuation_token="!!bad")


async def test_object_get_info_returns_head_fields(server, s3):
    s3.put_object(
        Bucket=BUCKET,
        Key="info.json",
        Body=b"{}",
        ContentType="application/json",
        Metadata={"owner": "team"},
    )
    info = await call(server, "object_get_info", bucket=BUCKET, key="info.json")
    assert info["size"] == 2
    assert info["size_human"] == "2 B"
    assert info["content_type"] == "application/json"
    assert info["metadata"] == {"owner": "team"}
    assert info["etag"]
    assert info["last_modified"]


async def test_object_get_info_missing_key_is_explained(server, s3):
    with pytest.raises(ToolError, match="HeadObject failed"):
        await call(server, "object_get_info", bucket=BUCKET, key="missing")


async def test_object_get_text_returns_whole_small_object(server, s3):
    s3.put_object(Bucket=BUCKET, Key="note.txt", Body="héllo".encode(), ContentType="text/plain")
    result = await call(server, "object_get_text", bucket=BUCKET, key="note.txt")
    assert result["text"] == "héllo"
    assert result["truncated"] is False
    assert result["size"] == 6
    assert "note" not in result


async def test_object_get_text_truncates_above_max_bytes_and_says_so(server, s3):
    s3.put_object(Bucket=BUCKET, Key="big.txt", Body=b"a" * 5000)
    result = await call(server, "object_get_text", bucket=BUCKET, key="big.txt", max_bytes=1000)
    assert result["text"] == "a" * 1000
    assert result["truncated"] is True
    assert result["size"] == 5000
    assert "presign_get" in result["note"]


async def test_object_get_text_truncation_never_splits_a_character(server, s3):
    s3.put_object(Bucket=BUCKET, Key="euro.txt", Body="€€€".encode())  # 3 bytes each
    result = await call(server, "object_get_text", bucket=BUCKET, key="euro.txt", max_bytes=4)
    assert result["text"] == "€"
    assert result["truncated"] is True


async def test_object_get_text_reads_empty_object(server, s3):
    s3.put_object(Bucket=BUCKET, Key="empty.txt", Body=b"")
    result = await call(server, "object_get_text", bucket=BUCKET, key="empty.txt")
    assert result["text"] == ""
    assert result["truncated"] is False


async def test_object_get_text_refuses_above_one_mib(server, s3):
    with pytest.raises(ToolError, match="1 MiB"):
        await call(server, "object_get_text", bucket=BUCKET, key="x", max_bytes=1_048_577)


async def test_object_get_text_refuses_binary_content(server, s3):
    s3.put_object(Bucket=BUCKET, Key="img.bin", Body=bytes(range(256)))
    with pytest.raises(ToolError, match="presign_get"):
        await call(server, "object_get_text", bucket=BUCKET, key="img.bin")


async def test_object_get_text_missing_key_is_explained(server, s3):
    with pytest.raises(ToolError, match="GetObject failed: NoSuchKey"):
        await call(server, "object_get_text", bucket=BUCKET, key="missing")


async def test_object_get_text_missing_bucket_keeps_the_region_lookup_error(server):
    with pytest.raises(ToolError, match="GetBucketLocation failed: NoSuchBucket"):
        await call(server, "object_get_text", bucket="no-such-bucket", key="k")


class _RawBody(io.BytesIO):
    """Stands in for urllib3's response so botocore can stream the body."""

    def stream(self, amt: int = 1024, **_: Any) -> Iterator[bytes]:
        while chunk := self.read(amt):
            yield chunk


def _wasabi_appended_get_object(body: bytes) -> Callable[..., Any]:
    """A ``before-send`` hook answering GetObject the way Wasabi does for an appended object.

    The CRC32 header covers only the first write, so it doesn't match the full body.
    """

    def respond(request: Any, **_: Any) -> AWSResponse | None:
        if request.method != "GET" or not urlsplit(request.url).path.endswith("/log.txt"):
            return None
        headers = {
            "Content-Type": "text/plain",
            "Content-Length": str(len(body)),
            "Content-Range": f"bytes 0-{len(body) - 1}/{len(body)}",
            "ETag": '"9b2cf535f27731c974343645a3985328-2"',
            "Last-Modified": "Wed, 18 Nov 2026 10:00:00 GMT",
            "Accept-Ranges": "bytes",
            "x-amz-checksum-crc32": "AAAAAA==",
            "x-amz-request-id": "8B1E3C1D2F4A5B6C",
            "Server": "WasabiS3/7.24.3301-2026-10-01-0b1a2c3d4e (head01)",
        }
        return AWSResponse(request.url, 206, headers, _RawBody(body))

    return respond


@pytest.fixture
def s3_hooks(monkeypatch) -> list[Callable[..., Any]]:
    """``before-send`` hooks added to every S3 client the server builds."""
    hooks: list[Callable[..., Any]] = []
    original = Clients._client

    def patched(self, service, region, endpoint, config):
        client = original(self, service, region, endpoint, config)
        if service == "s3":
            client.meta.events.register("before-send", dispatch, unique_id="test-s3-hooks")
        return client

    def dispatch(**kwargs: Any) -> Any:
        # Hooks are read per request, so tests can add them after clients are cached.
        for hook in hooks:
            if (response := hook(**kwargs)) is not None:
                return response
        return None

    monkeypatch.setattr(Clients, "_client", patched)
    return hooks


async def test_object_get_text_reads_an_appended_object(server, s3, moto_url, s3_hooks):
    await call(server, "object_put_text", bucket=BUCKET, key="log.txt", text="first line\n")
    # moto has no append extension, so answer it the way Wasabi does and record the result.
    host = urlsplit(moto_url).hostname
    with respx.mock:
        route = respx.route(method="PUT", host=host).mock(
            return_value=httpx.Response(200, headers={"ETag": '"e1-2"'})
        )
        await call(server, "object_append", bucket=BUCKET, key="log.txt", text="second line\n")
    assert route.calls.last.request.url.query == b"append"
    s3_hooks.append(_wasabi_appended_get_object(b"first line\nsecond line\n"))
    result = await call(server, "object_get_text", bucket=BUCKET, key="log.txt")
    assert result["text"] == "first line\nsecond line\n"
    assert result["size"] == 23
    assert result["truncated"] is False


def _failing_read(error: Exception) -> Callable[..., Any]:
    """An ``after-call`` hook whose GetObject body fails to read with ``error``."""

    class FailingBody:
        def read(self, *_: Any) -> bytes:
            raise error

    def swap(parsed: dict[str, Any], **_: Any) -> None:
        parsed["Body"] = FailingBody()

    return swap


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (FlexibleChecksumError(error_msg="Expected checksum AAAAAA== did not match"), "GetObject"),
        (
            AttributeError("'StreamingChecksumBody' object has no attribute 'strip'"),
            "GetObject failed while reading the object body: AttributeError",
        ),
    ],
)
async def test_object_get_text_body_read_errors_are_tool_errors(
    server, s3, monkeypatch, error, message
):
    s3.put_object(Bucket=BUCKET, Key="a.txt", Body=b"hello")
    original = Clients._client

    def patched(self, service, region, endpoint, config):
        client = original(self, service, region, endpoint, config)
        client.meta.events.register("after-call.s3.GetObject", _failing_read(error))
        return client

    monkeypatch.setattr(Clients, "_client", patched)
    with pytest.raises(ToolError, match=message) as raised:
        await call(server, "object_get_text", bucket=BUCKET, key="a.txt")
    assert FAKE_SECRET_KEY not in str(raised.value)


async def test_object_tagging_get_returns_tags(server, s3):
    s3.put_object(Bucket=BUCKET, Key="t", Body=b"x", Tagging="env=prod&team=data")
    result = await call(server, "object_tagging_get", bucket=BUCKET, key="t")
    assert result == {"configured": True, "tags": {"env": "prod", "team": "data"}}


async def test_object_tagging_get_without_tags_is_not_configured(server, s3):
    s3.put_object(Bucket=BUCKET, Key="t", Body=b"x")
    assert await call(server, "object_tagging_get", bucket=BUCKET, key="t") == {"configured": False}


async def test_object_search_filters_by_suffix_contains_and_size(server, s3):
    s3.put_object(Bucket=BUCKET, Key="logs/app-1.log", Body=b"x" * 10)
    s3.put_object(Bucket=BUCKET, Key="logs/app-2.log", Body=b"x" * 500)
    s3.put_object(Bucket=BUCKET, Key="logs/db-1.log", Body=b"x" * 500)
    s3.put_object(Bucket=BUCKET, Key="logs/app-3.txt", Body=b"x" * 500)
    result = await call(
        server,
        "object_search",
        bucket=BUCKET,
        prefix="logs/",
        suffix=".log",
        contains="app",
        min_size=100,
    )
    assert [item["key"] for item in result["items"]] == ["logs/app-2.log"]
    assert result["truncated"] is False
    assert result["scanned"] == 4


async def test_object_search_filters_by_modification_time(server, s3):
    s3.put_object(Bucket=BUCKET, Key="now.txt", Body=b"x")
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    hits = await call(server, "object_search", bucket=BUCKET, modified_after=past)
    assert [item["key"] for item in hits["items"]] == ["now.txt"]
    misses = await call(server, "object_search", bucket=BUCKET, modified_after=future)
    assert misses["items"] == []
    before = await call(server, "object_search", bucket=BUCKET, modified_before=past)
    assert before["items"] == []


async def test_object_search_stops_at_max_scan_and_resumes(server, s3):
    for index in range(6):
        s3.put_object(Bucket=BUCKET, Key=f"f{index}.csv", Body=b"x")
    first = await call(server, "object_search", bucket=BUCKET, suffix=".csv", max_scan=4)
    assert len(first["items"]) == 4
    assert first["truncated"] is True
    assert first["next_token"] == "f3.csv"
    rest = await call(
        server, "object_search", bucket=BUCKET, suffix=".csv", continuation_token="f3.csv"
    )
    assert [item["key"] for item in rest["items"]] == ["f4.csv", "f5.csv"]
    assert rest["truncated"] is False


async def test_object_search_reports_early_stop(server, s3):
    for index in range(3):
        s3.put_object(Bucket=BUCKET, Key=f"f{index}.csv", Body=b"x")
    result = await call(server, "object_search", bucket=BUCKET, suffix=".mp4", max_scan=2)
    assert result["items"] == []
    assert result["truncated"] is True
    assert "max_scan" in result["note"]


async def test_object_search_rejects_bad_time(server, s3):
    with pytest.raises(ToolError, match="modified_after"):
        await call(server, "object_search", bucket=BUCKET, modified_after="yesterday")


async def test_read_tools_work_in_read_only_mode(read_only_server, s3):
    s3.put_object(Bucket=BUCKET, Key="a.txt", Body=b"hi")
    assert (await call(read_only_server, "object_list", bucket=BUCKET))["items"]
    text = await call(read_only_server, "object_get_text", bucket=BUCKET, key="a.txt")
    assert text["text"] == "hi"


async def test_bucket_region_comes_from_get_bucket_location(server, settings, moto_url):
    client = boto3.client(
        "s3",
        region_name="eu-central-1",
        endpoint_url=moto_url,
        aws_access_key_id=FAKE_ACCESS_KEY,
        aws_secret_access_key=FAKE_SECRET_KEY,
    )
    client.create_bucket(
        Bucket="eu-bucket", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
    )
    client.put_object(Bucket="eu-bucket", Key="k", Body=b"x")
    result = await call(server, "object_list", bucket="eu-bucket")
    assert [item["key"] for item in result["items"]] == ["k"]
    assert any(cache.get("eu-bucket") == "eu-central-1" for cache in _REGIONS.values())

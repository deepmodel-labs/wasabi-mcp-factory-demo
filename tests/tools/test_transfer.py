import urllib.request
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import boto3
import pytest

from tests.conftest import FAKE_ACCESS_KEY, FAKE_SECRET_KEY
from tests.helpers import ToolError, call
from wasabi_mcp.clients import Clients
from wasabi_mcp.config import Settings
from wasabi_mcp.server import build_server
from wasabi_mcp.tools import transfer

BUCKET = "media-bucket"


def _client(moto_url, region="us-east-1"):
    return boto3.client(
        "s3",
        region_name=region,
        endpoint_url=moto_url,
        aws_access_key_id=FAKE_ACCESS_KEY,
        aws_secret_access_key=FAKE_SECRET_KEY,
    )


@pytest.fixture
def s3(moto_url):
    client = _client(moto_url)
    client.create_bucket(Bucket=BUCKET)
    return client


@pytest.fixture
def list_calls(monkeypatch):
    """Counts ListObjectsV2 calls made by the server's clients."""
    calls: list[dict] = []
    original = Clients._client

    def patched(self, service, region, endpoint, config):
        client = original(self, service, region, endpoint, config)
        client.meta.events.register(
            "before-call.s3.ListObjectsV2",
            lambda params, **_: calls.append(params),
            unique_id="test-count-list",
        )
        return client

    monkeypatch.setattr(Clients, "_client", patched)
    return calls


def _fetch(url, method="GET", data=None, headers=None):
    request = urllib.request.Request(url, method=method, data=data, headers=headers or {})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read()


def _credential(url):
    return parse_qs(urlparse(url).query)["X-Amz-Credential"][0]


async def test_s2_presign_every_mp4_under_media_across_pages(server, s3, monkeypatch, list_calls):
    monkeypatch.setattr(transfer, "_LIST_PAGE_SIZE", 3)
    mp4s = [f"media/clip-{index:02d}.mp4" for index in range(7)]
    others = ["media/clip-00.mov", "media/notes.txt", "media/sub/poster.jpg", "other/x.mp4"]
    for key in mp4s + others:
        s3.put_object(Bucket=BUCKET, Key=key, Body=key.encode())

    result = await call(
        server, "presign_get_for_prefix", bucket=BUCKET, prefix="media/", suffix=".mp4"
    )

    assert len(list_calls) > 1  # the listing was paginated
    assert sorted(item["key"] for item in result["items"]) == mp4s
    assert result["truncated"] is False
    assert result["next_token"] is None
    for item in result["items"]:
        assert item["size"] == len(item["key"])
        assert _fetch(item["url"]) == item["key"].encode()


async def test_presign_get_for_prefix_without_suffix_returns_every_key(server, s3):
    for key in ("a/1.txt", "a/2.bin", "b/3.txt"):
        s3.put_object(Bucket=BUCKET, Key=key, Body=b"x")
    result = await call(server, "presign_get_for_prefix", bucket=BUCKET, prefix="a/")
    assert [item["key"] for item in result["items"]] == ["a/1.txt", "a/2.bin"]


async def test_presign_get_for_prefix_stops_at_max_items_and_resumes(server, s3, monkeypatch):
    monkeypatch.setattr(transfer, "_LIST_PAGE_SIZE", 2)
    keys = [f"media/{index}.mp4" for index in range(5)]
    for key in keys:
        s3.put_object(Bucket=BUCKET, Key=key, Body=b"x")
    first = await call(
        server, "presign_get_for_prefix", bucket=BUCKET, prefix="media/", max_items=3
    )
    assert [item["key"] for item in first["items"]] == keys[:3]
    assert first["truncated"] is True
    rest = await call(
        server,
        "presign_get_for_prefix",
        bucket=BUCKET,
        prefix="media/",
        continuation_token=first["next_token"],
    )
    assert [item["key"] for item in rest["items"]] == keys[3:]
    assert rest["truncated"] is False


async def test_presign_get_for_prefix_with_no_matches(server, s3):
    s3.put_object(Bucket=BUCKET, Key="media/a.mov", Body=b"x")
    result = await call(
        server, "presign_get_for_prefix", bucket=BUCKET, prefix="media/", suffix=".mp4"
    )
    assert result["items"] == []
    assert result["truncated"] is False


async def test_presign_get_downloads_the_object(server, s3):
    s3.put_object(Bucket=BUCKET, Key="big.bin", Body=b"\x00\x01payload")
    result = await call(server, "presign_get", bucket=BUCKET, key="big.bin", expires_seconds=60)
    assert result["method"] == "GET"
    assert result["expires_seconds"] == 60
    assert parse_qs(urlparse(result["url"]).query)["X-Amz-Expires"] == ["60"]
    assert _fetch(result["url"]) == b"\x00\x01payload"


async def test_presign_put_uploads_with_content_type(server, s3):
    result = await call(server, "presign_put", bucket=BUCKET, key="up.csv", content_type="text/csv")
    assert result["headers"] == {"Content-Type": "text/csv"}
    _fetch(result["url"], method="PUT", data=b"a,b", headers=result["headers"])
    stored = s3.get_object(Bucket=BUCKET, Key="up.csv")
    assert stored["Body"].read() == b"a,b"
    assert stored["ContentType"] == "text/csv"


async def test_presign_put_refused_in_read_only_mode(read_only_server, s3):
    with pytest.raises(ToolError, match="read-only"):
        await call(read_only_server, "presign_put", bucket=BUCKET, key="k")


async def test_presign_get_works_in_read_only_mode(read_only_server, s3):
    s3.put_object(Bucket=BUCKET, Key="k", Body=b"x")
    result = await call(read_only_server, "presign_get", bucket=BUCKET, key="k")
    assert _fetch(result["url"]) == b"x"


@pytest.mark.parametrize("expires", [59, 604_801, 0])
@pytest.mark.parametrize("name", ["presign_get", "presign_put", "presign_get_for_prefix"])
async def test_expires_seconds_out_of_range_is_refused(server, s3, name, expires):
    arguments = {"prefix": "media/"} if name == "presign_get_for_prefix" else {"key": "k"}
    with pytest.raises(ToolError, match="expires_seconds"):
        await call(server, name, bucket=BUCKET, expires_seconds=expires, **arguments)


@pytest.mark.parametrize("expires", [60, 604_800])
async def test_expires_seconds_bounds_are_allowed(server, s3, expires):
    result = await call(server, "presign_get", bucket=BUCKET, key="k", expires_seconds=expires)
    assert result["expires_seconds"] == expires


def _assert_expires_about(result, seconds):
    expires_at = datetime.fromisoformat(result["expires_at"])
    remaining = (expires_at - datetime.now(UTC)).total_seconds()
    assert seconds - 30 <= remaining <= seconds


async def test_read_only_presign_get_caps_expiry_at_an_hour(read_only_server, s3):
    s3.put_object(Bucket=BUCKET, Key="k", Body=b"x")
    result = await call(
        read_only_server, "presign_get", bucket=BUCKET, key="k", expires_seconds=604_800
    )
    assert parse_qs(urlparse(result["url"]).query)["X-Amz-Expires"] == ["3600"]
    assert result["expires_seconds"] == 3_600
    _assert_expires_about(result, 3_600)
    assert "Read-only mode capped" in result["note"]
    assert "604800" in result["note"]
    assert _fetch(result["url"]) == b"x"


async def test_read_only_presign_get_for_prefix_caps_every_url(read_only_server, s3):
    for key in ("media/a.mp4", "media/b.mp4", "media/c.mp4"):
        s3.put_object(Bucket=BUCKET, Key=key, Body=b"x")
    result = await call(
        read_only_server,
        "presign_get_for_prefix",
        bucket=BUCKET,
        prefix="media/",
        expires_seconds=604_800,
    )
    assert len(result["items"]) == 3
    for item in result["items"]:
        assert parse_qs(urlparse(item["url"]).query)["X-Amz-Expires"] == ["3600"]
    assert result["expires_seconds"] == 3_600
    _assert_expires_about(result, 3_600)
    assert "Read-only mode capped" in result["note"]


@pytest.mark.parametrize("expires", [60, 1_800, 3_600])
@pytest.mark.parametrize("name", ["presign_get", "presign_get_for_prefix"])
async def test_read_only_expiry_at_or_under_an_hour_is_honored(read_only_server, s3, name, expires):
    s3.put_object(Bucket=BUCKET, Key="media/a.mp4", Body=b"x")
    arguments = {"prefix": "media/"} if name == "presign_get_for_prefix" else {"key": "k"}
    result = await call(read_only_server, name, bucket=BUCKET, expires_seconds=expires, **arguments)
    url = result["items"][0]["url"] if name == "presign_get_for_prefix" else result["url"]
    assert parse_qs(urlparse(url).query)["X-Amz-Expires"] == [str(expires)]
    assert result["expires_seconds"] == expires
    assert "note" not in result


@pytest.mark.parametrize("name", ["presign_get", "presign_get_for_prefix"])
async def test_read_only_still_refuses_out_of_range_expiry(read_only_server, s3, name):
    arguments = {"prefix": "media/"} if name == "presign_get_for_prefix" else {"key": "k"}
    with pytest.raises(ToolError, match="expires_seconds"):
        await call(read_only_server, name, bucket=BUCKET, expires_seconds=604_801, **arguments)


async def test_writable_presign_get_keeps_seven_day_expiry(server, s3):
    result = await call(server, "presign_get", bucket=BUCKET, key="k", expires_seconds=604_800)
    assert parse_qs(urlparse(result["url"]).query)["X-Amz-Expires"] == ["604800"]
    assert result["expires_seconds"] == 604_800
    _assert_expires_about(result, 604_800)
    assert "note" not in result


async def test_presigned_urls_use_the_endpoint_for_the_given_region():
    settings = Settings(
        access_key_id=FAKE_ACCESS_KEY,
        secret_access_key=FAKE_SECRET_KEY,
        region="us-east-1",
        read_only=False,
    )
    server = build_server(settings)
    for name in ("presign_get", "presign_put"):
        result = await call(server, name, bucket="b", key="k", region="eu-central-1")
        url = urlparse(result["url"])
        assert f"{url.scheme}://{url.netloc}" == settings.s3_endpoint_for("eu-central-1")
        assert url.netloc == "s3.eu-central-1.wasabisys.com"
        assert url.path == "/b/k"
        assert "/eu-central-1/s3/" in _credential(result["url"])


async def test_presigned_urls_use_the_bucket_region_from_get_bucket_location(
    server, settings, moto_url
):
    regional = _client(moto_url, "eu-central-1")
    regional.create_bucket(
        Bucket="eu-media", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
    )
    regional.put_object(Bucket="eu-media", Key="media/a.mp4", Body=b"eu")
    single = await call(server, "presign_get", bucket="eu-media", key="media/a.mp4")
    listed = await call(
        server, "presign_get_for_prefix", bucket="eu-media", prefix="media/", suffix=".mp4"
    )
    for url in (single["url"], listed["items"][0]["url"]):
        assert url.startswith(settings.s3_endpoint_for("eu-central-1") + "/eu-media/")
        assert "/eu-central-1/s3/" in _credential(url)
        assert _fetch(url) == b"eu"


async def test_presign_get_for_prefix_missing_bucket_is_explained(server, moto_url):
    with pytest.raises(ToolError, match="failed"):
        await call(server, "presign_get_for_prefix", bucket="no-such-bucket", prefix="media/")


async def test_multipart_presign_upload_parts_and_complete(server, s3):
    part = b"a" * (5 * 1024 * 1024)
    started = await call(
        server, "multipart_upload_presign", bucket=BUCKET, key="big.bin", part_count=2
    )
    assert started["upload_id"]
    assert [p["part_number"] for p in started["parts"]] == [1, 2]
    etags = []
    for presigned, body in zip(started["parts"], (part, b"tail"), strict=True):
        # An explicit Content-Type stops moto's Flask app parsing the body as a form.
        request = urllib.request.Request(
            presigned["url"],
            method="PUT",
            data=body,
            headers={"Content-Type": "application/octet-stream"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            etags.append(response.headers["ETag"])

    listed = await call(server, "multipart_upload_list", bucket=BUCKET)
    assert [(u["key"], u["upload_id"]) for u in listed["items"]] == [
        ("big.bin", started["upload_id"])
    ]
    assert listed["items"][0]["initiated"]
    assert listed["truncated"] is False
    part_list = await call(
        server, "multipart_part_list", bucket=BUCKET, key="big.bin", upload_id=started["upload_id"]
    )
    assert [(p["part_number"], p["etag"]) for p in part_list["items"]] == [
        (1, etags[0]),
        (2, etags[1]),
    ]

    done = await call(
        server,
        "multipart_upload_complete",
        bucket=BUCKET,
        key="big.bin",
        upload_id=started["upload_id"],
        parts=[{"part_number": n, "etag": e} for n, e in ((2, etags[1]), (1, etags[0]))],
    )
    assert done["etag"]
    assert s3.get_object(Bucket=BUCKET, Key="big.bin")["Body"].read() == part + b"tail"
    assert (await call(server, "multipart_upload_list", bucket=BUCKET))["items"] == []


@pytest.fixture
def upload_list_calls(monkeypatch):
    """Records ListMultipartUploads parameters and fakes a truncated first page.

    moto ignores MaxUploads, so paging is checked against the parameters sent.
    """
    calls: list[dict] = []
    original = Clients._client

    def patched(self, service, region, endpoint, config):
        client = original(self, service, region, endpoint, config)

        def record(params, **_):
            calls.append(dict(params))

        def truncate(parsed, **_):
            if len(calls) == 1:
                parsed.update(IsTruncated=True, NextKeyMarker="k1", NextUploadIdMarker="upload-1")

        client.meta.events.register(
            "before-parameter-build.s3.ListMultipartUploads",
            record,
            unique_id="test-record-uploads",
        )
        client.meta.events.register(
            "after-call.s3.ListMultipartUploads", truncate, unique_id="test-truncate-uploads"
        )
        return client

    monkeypatch.setattr(Clients, "_client", patched)
    return calls


async def test_multipart_upload_list_paginates(server, s3, upload_list_calls):
    s3.create_multipart_upload(Bucket=BUCKET, Key="k0")
    first = await call(server, "multipart_upload_list", bucket=BUCKET, prefix="k", max_items=2)
    assert first["truncated"] is True
    await call(
        server, "multipart_upload_list", bucket=BUCKET, continuation_token=first["next_token"]
    )
    assert upload_list_calls[0]["MaxUploads"] == 2
    assert upload_list_calls[0]["Prefix"] == "k"
    assert upload_list_calls[1]["KeyMarker"] == "k1"
    assert upload_list_calls[1]["UploadIdMarker"] == "upload-1"


async def test_multipart_upload_list_rejects_a_foreign_token(server, s3):
    with pytest.raises(ToolError, match="continuation_token"):
        await call(server, "multipart_upload_list", bucket=BUCKET, continuation_token="!!")


async def test_multipart_upload_abort_needs_confirm_then_aborts(server, s3):
    upload_id = s3.create_multipart_upload(Bucket=BUCKET, Key="k")["UploadId"]
    with pytest.raises(ToolError, match="confirm=true"):
        await call(server, "multipart_upload_abort", bucket=BUCKET, key="k", upload_id=upload_id)
    assert s3.list_multipart_uploads(Bucket=BUCKET).get("Uploads")
    result = await call(
        server, "multipart_upload_abort", bucket=BUCKET, key="k", upload_id=upload_id, confirm=True
    )
    assert result["aborted"] is True
    assert not s3.list_multipart_uploads(Bucket=BUCKET).get("Uploads")


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("multipart_upload_presign", {"part_count": 1}),
        (
            "multipart_upload_complete",
            {"upload_id": "u", "parts": [{"part_number": 1, "etag": "e"}]},
        ),
        ("multipart_upload_abort", {"upload_id": "u", "confirm": True}),
    ],
)
async def test_multipart_writes_refused_in_read_only_mode(read_only_server, s3, name, arguments):
    with pytest.raises(ToolError, match="read-only"):
        await call(read_only_server, name, bucket=BUCKET, key="k", **arguments)
    assert not s3.list_multipart_uploads(Bucket=BUCKET).get("Uploads")


async def test_multipart_reads_work_in_read_only_mode(read_only_server, s3):
    upload_id = s3.create_multipart_upload(Bucket=BUCKET, Key="k")["UploadId"]
    listed = await call(read_only_server, "multipart_upload_list", bucket=BUCKET)
    assert listed["items"][0]["upload_id"] == upload_id
    parts = await call(
        read_only_server, "multipart_part_list", bucket=BUCKET, key="k", upload_id=upload_id
    )
    assert parts["items"] == []


@pytest.mark.parametrize("part_count", [0, 10_001])
async def test_multipart_presign_part_count_out_of_range_is_refused(server, s3, part_count):
    with pytest.raises(ToolError, match="part_count"):
        await call(
            server, "multipart_upload_presign", bucket=BUCKET, key="k", part_count=part_count
        )
    assert not s3.list_multipart_uploads(Bucket=BUCKET).get("Uploads")


async def test_multipart_presign_expires_out_of_range_is_refused(server, s3):
    with pytest.raises(ToolError, match="expires_seconds"):
        await call(
            server,
            "multipart_upload_presign",
            bucket=BUCKET,
            key="k",
            part_count=1,
            expires_seconds=59,
        )
    assert not s3.list_multipart_uploads(Bucket=BUCKET).get("Uploads")


@pytest.mark.parametrize("parts", [[], [{"part_number": 0, "etag": "e"}], [{"part_number": 1}]])
async def test_multipart_complete_rejects_bad_parts(server, s3, parts):
    with pytest.raises(ToolError, match="part"):
        await call(
            server, "multipart_upload_complete", bucket=BUCKET, key="k", upload_id="u", parts=parts
        )


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("multipart_part_list", {}),
        ("multipart_upload_abort", {"confirm": True}),
    ],
)
async def test_multipart_unknown_upload_is_explained(server, s3, name, arguments):
    with pytest.raises(ToolError, match="NoSuchUpload"):
        await call(server, name, bucket=BUCKET, key="k", upload_id="missing", **arguments)


async def test_multipart_missing_bucket_is_explained(server, moto_url):
    with pytest.raises(ToolError, match="failed"):
        await call(server, "multipart_upload_list", bucket="no-such-bucket")

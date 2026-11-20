import pytest

from tests.helpers import ToolError, call
from wasabi_mcp.clients import Clients
from wasabi_mcp.tools import DESTRUCTIVE, READ, WRITE

KINDS = {
    "bucket_cors_get": READ,
    "bucket_cors_set": WRITE,
    "bucket_cors_delete": DESTRUCTIVE,
    "bucket_lifecycle_get": READ,
    "bucket_lifecycle_set": DESTRUCTIVE,
    "bucket_lifecycle_delete": DESTRUCTIVE,
}

BUCKET = "rules-bucket"
CORS = [
    {
        "allowed_origins": ["https://app.example.com"],
        "allowed_methods": ["get", "PUT"],
        "allowed_headers": ["*"],
        "expose_headers": ["ETag"],
        "max_age_seconds": 600,
    }
]
S3_CORS = [
    {
        "AllowedOrigins": ["https://app.example.com"],
        "AllowedMethods": ["GET", "PUT"],
        "AllowedHeaders": ["*"],
        "ExposeHeaders": ["ETag"],
        "MaxAgeSeconds": 600,
    }
]
TMP_EXPIRY = [{"ID": "tmp-90-days", "Filter": {"Prefix": "tmp/"}, "Expiration": {"Days": 90}}]


@pytest.fixture
def s3(settings):
    client = Clients(settings).s3()
    client.create_bucket(Bucket=BUCKET)
    return client


async def test_tools_are_registered_with_their_kinds(server):
    tools = {tool.name: tool for tool in await server.list_tools()}
    for name, kind in KINDS.items():
        assert tools[name].annotations == kind, name
    assert "permissive default" in tools["bucket_cors_get"].description
    lifecycle = tools["bucket_lifecycle_set"].description
    for phrase in ("00:00 UTC", "24-48 hours", "locked objects", "MFA Delete"):
        assert phrase in lifecycle


# CORS


async def test_bucket_cors_get_unconfigured(server, s3):
    assert await call(server, "bucket_cors_get", bucket=BUCKET) == {"configured": False}


async def test_bucket_cors_set_get_and_delete(server, s3):
    result = await call(server, "bucket_cors_set", bucket=BUCKET, rules=CORS)
    assert result == {"ok": True, "bucket": BUCKET, "rules": S3_CORS}
    got = await call(server, "bucket_cors_get", bucket=BUCKET)
    assert got["configured"] is True
    assert got["rules"][0]["AllowedMethods"] == ["GET", "PUT"]
    assert got["rules"][0]["AllowedOrigins"] == ["https://app.example.com"]
    assert await call(server, "bucket_cors_delete", bucket=BUCKET, confirm=True) == {
        "ok": True,
        "bucket": BUCKET,
    }
    assert await call(server, "bucket_cors_get", bucket=BUCKET) == {"configured": False}


@pytest.mark.parametrize(
    ("rules", "message"),
    [
        ([], "at least one CORS rule"),
        ([{"allowed_origins": ["*"]}], "needs allowed_origins and allowed_methods"),
        ([{"allowed_origins": ["*"], "allowed_methods": ["PATCH"]}], "unsupported methods"),
        ([{"allowed_origins": ["*"], "allowed_methods": ["GET"], "origin": "x"}], "unknown"),
    ],
)
async def test_bucket_cors_set_rejects_bad_rules_before_calling(server, s3, rules, message):
    with pytest.raises(ToolError, match=message):
        await call(server, "bucket_cors_set", bucket=BUCKET, rules=rules)
    assert await call(server, "bucket_cors_get", bucket=BUCKET) == {"configured": False}


async def test_bucket_cors_set_refused_when_read_only(read_only_server, s3):
    with pytest.raises(ToolError, match="PutBucketCors refused: the server is read-only"):
        await call(read_only_server, "bucket_cors_set", bucket=BUCKET, rules=CORS)
    assert await call(read_only_server, "bucket_cors_get", bucket=BUCKET) == {"configured": False}


async def test_bucket_cors_delete_needs_confirm(server, s3):
    s3.put_bucket_cors(Bucket=BUCKET, CORSConfiguration={"CORSRules": S3_CORS})
    with pytest.raises(ToolError, match="DeleteBucketCors refused: this is destructive"):
        await call(server, "bucket_cors_delete", bucket=BUCKET)
    assert (await call(server, "bucket_cors_get", bucket=BUCKET))["configured"] is True


async def test_bucket_cors_delete_refused_when_read_only(read_only_server, s3):
    s3.put_bucket_cors(Bucket=BUCKET, CORSConfiguration={"CORSRules": S3_CORS})
    with pytest.raises(ToolError, match="DeleteBucketCors refused: the server is read-only"):
        await call(read_only_server, "bucket_cors_delete", bucket=BUCKET, confirm=True)
    assert (await call(read_only_server, "bucket_cors_get", bucket=BUCKET))["configured"] is True


async def test_bucket_cors_get_missing_bucket(server):
    with pytest.raises(ToolError, match="GetBucketCors failed: NoSuchBucket: "):
        await call(server, "bucket_cors_get", bucket="no-such-bucket")


# lifecycle


async def test_bucket_lifecycle_get_unconfigured(server, s3):
    assert await call(server, "bucket_lifecycle_get", bucket=BUCKET) == {"configured": False}


async def test_bucket_lifecycle_set_90_day_tmp_expiry(server, s3):
    """Scenario S5: a 90-day expiry for everything under tmp/."""
    result = await call(
        server, "bucket_lifecycle_set", bucket=BUCKET, rules=TMP_EXPIRY, confirm=True
    )
    assert result == {
        "ok": True,
        "bucket": BUCKET,
        "rules": [{**TMP_EXPIRY[0], "Status": "Enabled"}],
    }
    got = await call(server, "bucket_lifecycle_get", bucket=BUCKET)
    assert got["configured"] is True
    [rule] = got["rules"]
    assert rule["ID"] == "tmp-90-days"
    assert rule["Status"] == "Enabled"
    assert rule["Filter"] == {"Prefix": "tmp/"}
    assert rule["Expiration"]["Days"] == 90


async def test_bucket_lifecycle_set_defaults_to_whole_bucket(server, s3):
    rules = [{"ID": "uploads", "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}}]
    result = await call(server, "bucket_lifecycle_set", bucket=BUCKET, rules=rules, confirm=True)
    assert result["rules"][0]["Filter"] == {"Prefix": ""}
    rule = s3.get_bucket_lifecycle_configuration(Bucket=BUCKET)["Rules"][0]
    assert rule["AbortIncompleteMultipartUpload"] == {"DaysAfterInitiation": 7}


async def test_bucket_lifecycle_set_and_delete(server, s3):
    await call(server, "bucket_lifecycle_set", bucket=BUCKET, rules=TMP_EXPIRY, confirm=True)
    assert await call(server, "bucket_lifecycle_delete", bucket=BUCKET, confirm=True) == {
        "ok": True,
        "bucket": BUCKET,
    }
    assert await call(server, "bucket_lifecycle_get", bucket=BUCKET) == {"configured": False}


@pytest.mark.parametrize(
    ("rules", "message"),
    [
        (
            [{"ID": "x", "Transitions": [{"Days": 30, "StorageClass": "GLACIER"}]}],
            "transitions aren't supported",
        ),
        (
            [{"NoncurrentVersionTransitions": [{"NoncurrentDays": 30}]}],
            "transitions aren't supported",
        ),
        ([], "at least one lifecycle rule"),
        ([{"ID": "x", "Filter": {"Prefix": "a/"}}], "needs an action"),
        ([{"Expiration": {"Days": 1}, "Status": "On"}], "Status must be Enabled or Disabled"),
        ([{"Expiration": {"Days": 1}, "Filter": {"Suffix": ".tmp"}}], "Filter has unknown"),
        ([{"Expiration": {"Days": 1}, "Storage": "x"}], "unknown fields"),
    ],
)
async def test_bucket_lifecycle_set_rejects_bad_rules_before_calling(server, s3, rules, message):
    with pytest.raises(ToolError, match=message):
        await call(server, "bucket_lifecycle_set", bucket=BUCKET, rules=rules, confirm=True)
    assert await call(server, "bucket_lifecycle_get", bucket=BUCKET) == {"configured": False}


async def test_bucket_lifecycle_set_needs_confirm(server, s3):
    with pytest.raises(ToolError, match="PutBucketLifecycleConfiguration refused: this is"):
        await call(server, "bucket_lifecycle_set", bucket=BUCKET, rules=TMP_EXPIRY)
    assert await call(server, "bucket_lifecycle_get", bucket=BUCKET) == {"configured": False}


async def test_bucket_lifecycle_set_refused_when_read_only(read_only_server, s3):
    with pytest.raises(ToolError, match="PutBucketLifecycleConfiguration refused: the server"):
        await call(
            read_only_server, "bucket_lifecycle_set", bucket=BUCKET, rules=TMP_EXPIRY, confirm=True
        )
    assert await call(read_only_server, "bucket_lifecycle_get", bucket=BUCKET) == {
        "configured": False
    }


async def test_bucket_lifecycle_delete_needs_confirm(server, s3):
    await call(server, "bucket_lifecycle_set", bucket=BUCKET, rules=TMP_EXPIRY, confirm=True)
    with pytest.raises(ToolError, match="DeleteBucketLifecycle refused: this is destructive"):
        await call(server, "bucket_lifecycle_delete", bucket=BUCKET)
    assert (await call(server, "bucket_lifecycle_get", bucket=BUCKET))["configured"] is True


async def test_bucket_lifecycle_delete_refused_when_read_only(read_only_server, s3):
    s3.put_bucket_lifecycle_configuration(
        Bucket=BUCKET,
        LifecycleConfiguration={"Rules": [{**TMP_EXPIRY[0], "Status": "Enabled"}]},
    )
    with pytest.raises(ToolError, match="DeleteBucketLifecycle refused: the server is read-only"):
        await call(read_only_server, "bucket_lifecycle_delete", bucket=BUCKET, confirm=True)
    assert s3.get_bucket_lifecycle_configuration(Bucket=BUCKET)["Rules"]


async def test_bucket_lifecycle_set_missing_bucket(server):
    with pytest.raises(ToolError, match="GetBucketLocation failed: NoSuchBucket: "):
        await call(
            server, "bucket_lifecycle_set", bucket="no-such-bucket", rules=TMP_EXPIRY, confirm=True
        )

from datetime import UTC, datetime, timedelta

import pytest
from botocore.stub import Stubber
from mcp.server.mcpserver import MCPServer

from tests.helpers import ToolError, call
from wasabi_mcp.clients import Clients
from wasabi_mcp.tools import DESTRUCTIVE, READ, ToolContext, object_lock

KINDS = {
    "bucket_object_lock_get": READ,
    "bucket_object_lock_set": DESTRUCTIVE,
    "object_retention_get": READ,
    "object_retention_set": DESTRUCTIVE,
    "object_legal_hold_get": READ,
    "object_legal_hold_set": DESTRUCTIVE,
}

LOCKED = "locked-bucket"
PLAIN = "plain-bucket"
KEY = "records/2026.csv"


@pytest.fixture
def s3(settings):
    client = Clients(settings).s3()
    client.create_bucket(Bucket=LOCKED, ObjectLockEnabledForBucket=True)
    client.create_bucket(Bucket=PLAIN)
    client.put_object(Bucket=LOCKED, Key=KEY, Body=b"a,b\n")
    return client


def _later(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).replace(microsecond=0).isoformat()


def _stubbed_server(settings):
    """A server whose S3 client is stubbed, for responses moto doesn't model."""
    clients = Clients(settings)
    server = MCPServer("test")
    object_lock.register(server, ToolContext(settings=settings, clients=clients))
    return server, Stubber(clients.s3())


async def test_tools_are_registered_with_their_kinds(server):
    tools = {tool.name: tool for tool in await server.list_tools()}
    for name, kind in KINDS.items():
        assert tools[name].annotations == kind, name
    assert (
        "only be enabled when the bucket is created" in tools["bucket_object_lock_set"].description
    )
    assert "can't be shortened" in tools["object_retention_set"].description


# bucket default retention


async def test_seven_year_compliance_lock(server):
    """Scenario S9: a lock-enabled bucket with a 7-year compliance default."""
    await call(server, "bucket_create", bucket="vault-bucket", object_lock=True)
    result = await call(
        server,
        "bucket_object_lock_set",
        bucket="vault-bucket",
        mode="COMPLIANCE",
        years=7,
        confirm=True,
    )
    assert result == {
        "ok": True,
        "bucket": "vault-bucket",
        "enabled": True,
        "default_retention": {"Mode": "COMPLIANCE", "Years": 7},
    }
    assert await call(server, "bucket_object_lock_get", bucket="vault-bucket") == {
        "bucket": "vault-bucket",
        "enabled": True,
        "default_retention": {"Mode": "COMPLIANCE", "Years": 7},
    }


async def test_bucket_object_lock_get_without_lock(server, s3):
    assert await call(server, "bucket_object_lock_get", bucket=PLAIN) == {
        "bucket": PLAIN,
        "enabled": False,
        "default_retention": None,
    }


async def test_bucket_object_lock_get_enabled_without_rule(server, s3):
    result = await call(server, "bucket_object_lock_get", bucket=LOCKED)
    assert result == {"bucket": LOCKED, "enabled": True, "default_retention": None}


async def test_bucket_object_lock_set_days_then_clear_default(server, s3):
    # moto only accepts a default rule on an empty bucket.
    s3.create_bucket(Bucket="empty-locked", ObjectLockEnabledForBucket=True)
    await call(
        server,
        "bucket_object_lock_set",
        bucket="empty-locked",
        mode="governance",
        days=30,
        confirm=True,
    )
    got = await call(server, "bucket_object_lock_get", bucket="empty-locked")
    assert got["default_retention"] == {"Mode": "GOVERNANCE", "Days": 30}
    result = await call(
        server, "bucket_object_lock_set", bucket="empty-locked", clear_default=True, confirm=True
    )
    assert result["default_retention"] is None
    assert (await call(server, "bucket_object_lock_get", bucket="empty-locked"))[
        "default_retention"
    ] is None


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"mode": "COMPLIANCE", "days": 1, "years": 1}, "exactly one of days or years"),
        ({"mode": "COMPLIANCE"}, "exactly one of days or years"),
        ({"days": 1}, "Give mode"),
        ({"mode": "LEGAL", "days": 1}, "mode must be COMPLIANCE or GOVERNANCE"),
        ({"mode": "COMPLIANCE", "days": 0}, "at least 1"),
        ({"clear_default": True, "days": 1}, "clear_default=true takes no"),
    ],
)
async def test_bucket_object_lock_set_validates_before_calling(server, s3, arguments, message):
    with pytest.raises(ToolError, match=message):
        await call(server, "bucket_object_lock_set", bucket=LOCKED, confirm=True, **arguments)
    assert (await call(server, "bucket_object_lock_get", bucket=LOCKED))[
        "default_retention"
    ] is None


async def test_bucket_object_lock_set_needs_confirm(server, s3):
    with pytest.raises(ToolError, match="PutObjectLockConfiguration refused: this is destructive"):
        await call(server, "bucket_object_lock_set", bucket=LOCKED, mode="COMPLIANCE", years=7)
    assert (await call(server, "bucket_object_lock_get", bucket=LOCKED))[
        "default_retention"
    ] is None


async def test_bucket_object_lock_set_refused_when_read_only(read_only_server, s3):
    with pytest.raises(ToolError, match="PutObjectLockConfiguration refused: the server is"):
        await call(
            read_only_server,
            "bucket_object_lock_set",
            bucket=LOCKED,
            mode="COMPLIANCE",
            years=7,
            confirm=True,
        )
    lock = s3.get_object_lock_configuration(Bucket=LOCKED)["ObjectLockConfiguration"]
    assert "Rule" not in lock


async def test_bucket_object_lock_set_on_unlocked_bucket_reports_wasabi_error(server, s3):
    with pytest.raises(ToolError, match="PutObjectLockConfiguration failed: InvalidBucketState"):
        await call(
            server, "bucket_object_lock_set", bucket=PLAIN, mode="COMPLIANCE", days=1, confirm=True
        )


# object retention


async def test_object_retention_set_and_get(server, s3):
    until = _later(30)
    result = await call(
        server,
        "object_retention_set",
        bucket=LOCKED,
        key=KEY,
        mode="GOVERNANCE",
        retain_until=until,
        confirm=True,
    )
    assert result["ok"] is True
    assert result["mode"] == "GOVERNANCE"
    assert result["retain_until"] == until
    # moto 5.2's GetObjectRetention always fails, so check the object's headers instead.
    head = s3.head_object(Bucket=LOCKED, Key=KEY)
    assert head["ObjectLockMode"] == "GOVERNANCE"
    assert head["ObjectLockRetainUntilDate"] == datetime.fromisoformat(until)


async def test_object_retention_get(settings):
    server, stubber = _stubbed_server(settings)
    until = datetime(2030, 1, 1, tzinfo=UTC)
    stubber.add_response(
        "get_object_retention",
        {"Retention": {"Mode": "COMPLIANCE", "RetainUntilDate": until}},
        {"Bucket": LOCKED, "Key": KEY, "VersionId": "v1"},
    )
    with stubber:
        result = await call(server, "object_retention_get", bucket=LOCKED, key=KEY, version_id="v1")
    assert result == {
        "configured": True,
        "bucket": LOCKED,
        "key": KEY,
        "version_id": "v1",
        "mode": "COMPLIANCE",
        "retain_until": "2030-01-01T00:00:00+00:00",
    }


async def test_object_retention_set_naive_time_is_utc(server, s3):
    naive = (datetime.now(UTC) + timedelta(days=5)).replace(microsecond=0, tzinfo=None)
    result = await call(
        server,
        "object_retention_set",
        bucket=LOCKED,
        key=KEY,
        mode="GOVERNANCE",
        retain_until=naive.isoformat(),
        confirm=True,
    )
    assert result["retain_until"] == naive.replace(tzinfo=UTC).isoformat()


async def test_object_retention_set_bypass_governance_sends_the_header(settings, s3):
    server, stubber = _stubbed_server(settings)
    stubber.add_response("get_bucket_location", {"LocationConstraint": ""}, {"Bucket": LOCKED})
    until = datetime.fromisoformat(_later(1))
    stubber.add_response(
        "put_object_retention",
        {},
        {
            "Bucket": LOCKED,
            "Key": KEY,
            "BypassGovernanceRetention": True,
            "Retention": {"Mode": "GOVERNANCE", "RetainUntilDate": until},
        },
    )
    with stubber:
        await call(
            server,
            "object_retention_set",
            bucket=LOCKED,
            key=KEY,
            mode="GOVERNANCE",
            retain_until=until.isoformat(),
            bypass_governance=True,
            confirm=True,
        )
    stubber.assert_no_pending_responses()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"mode": "GOVERNANCE", "retain_until": "next week"}, "retain_until must be ISO 8601"),
        ({"mode": "FOREVER", "retain_until": "2030-01-01"}, "mode must be COMPLIANCE"),
    ],
)
async def test_object_retention_set_validates_before_calling(server, s3, arguments, message):
    with pytest.raises(ToolError, match=message):
        await call(
            server, "object_retention_set", bucket=LOCKED, key=KEY, confirm=True, **arguments
        )


async def test_object_retention_set_needs_confirm(server, s3):
    with pytest.raises(ToolError, match="PutObjectRetention refused: this is destructive"):
        await call(
            server,
            "object_retention_set",
            bucket=LOCKED,
            key=KEY,
            mode="COMPLIANCE",
            retain_until=_later(1),
        )


async def test_object_retention_set_refused_when_read_only(read_only_server, settings, s3):
    with pytest.raises(ToolError, match="PutObjectRetention refused: the server is read-only"):
        await call(
            read_only_server,
            "object_retention_set",
            bucket=LOCKED,
            key=KEY,
            mode="COMPLIANCE",
            retain_until=_later(1),
            confirm=True,
        )
    # The object stays deletable, so no retention was set.
    s3.delete_object(Bucket=LOCKED, Key=KEY)


async def test_object_retention_get_unconfigured(settings):
    server, stubber = _stubbed_server(settings)
    stubber.add_client_error(
        "get_object_retention",
        service_error_code="NoSuchObjectLockConfiguration",
        service_message="The specified object does not have a ObjectLock configuration",
    )
    with stubber:
        result = await call(server, "object_retention_get", bucket=LOCKED, key=KEY)
    assert result == {"configured": False}


async def test_object_retention_get_missing_key(settings):
    server, stubber = _stubbed_server(settings)
    stubber.add_client_error(
        "get_object_retention", service_error_code="NoSuchKey", service_message="No such key"
    )
    with stubber, pytest.raises(ToolError, match="GetObjectRetention failed: NoSuchKey: No such"):
        await call(server, "object_retention_get", bucket=LOCKED, key="missing")


# legal hold


async def test_object_legal_hold_set_and_get(server, s3):
    assert (await call(server, "object_legal_hold_get", bucket=LOCKED, key=KEY))["on"] is False
    result = await call(
        server, "object_legal_hold_set", bucket=LOCKED, key=KEY, on=True, confirm=True
    )
    assert result == {"ok": True, "bucket": LOCKED, "key": KEY, "version_id": None, "on": True}
    assert await call(server, "object_legal_hold_get", bucket=LOCKED, key=KEY) == {
        "bucket": LOCKED,
        "key": KEY,
        "version_id": None,
        "on": True,
    }
    await call(server, "object_legal_hold_set", bucket=LOCKED, key=KEY, on=False, confirm=True)
    assert (await call(server, "object_legal_hold_get", bucket=LOCKED, key=KEY))["on"] is False


async def test_object_legal_hold_set_one_version(server, s3):
    version = s3.head_object(Bucket=LOCKED, Key=KEY)["VersionId"]
    await call(
        server,
        "object_legal_hold_set",
        bucket=LOCKED,
        key=KEY,
        on=True,
        version_id=version,
        confirm=True,
    )
    got = await call(server, "object_legal_hold_get", bucket=LOCKED, key=KEY, version_id=version)
    assert got["on"] is True
    assert got["version_id"] == version


async def test_object_legal_hold_set_needs_confirm(server, s3):
    with pytest.raises(ToolError, match="PutObjectLegalHold refused: this is destructive"):
        await call(server, "object_legal_hold_set", bucket=LOCKED, key=KEY, on=True)
    assert (await call(server, "object_legal_hold_get", bucket=LOCKED, key=KEY))["on"] is False


async def test_object_legal_hold_set_refused_when_read_only(read_only_server, s3):
    with pytest.raises(ToolError, match="PutObjectLegalHold refused: the server is read-only"):
        await call(
            read_only_server, "object_legal_hold_set", bucket=LOCKED, key=KEY, on=True, confirm=True
        )
    legal_hold = s3.get_object_legal_hold(Bucket=LOCKED, Key=KEY).get("LegalHold", {})
    assert legal_hold.get("Status") != "ON"


async def test_object_legal_hold_get_missing_bucket(server):
    with pytest.raises(ToolError, match="GetObjectLegalHold failed: NoSuchBucket: "):
        await call(server, "object_legal_hold_get", bucket="no-such-bucket", key=KEY)

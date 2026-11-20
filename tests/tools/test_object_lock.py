from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import boto3
import pytest

from tests.conftest import FAKE_ACCESS_KEY, FAKE_SECRET_KEY
from tests.helpers import ToolError, call
from wasabi_mcp.clients import Clients

BUCKET = "locked"
PLAIN = "plain"

LOCK_CALLS = [
    (
        "object_retention_set",
        {"mode": "GOVERNANCE", "retain_until": "2099-01-01T00:00:00+00:00"},
    ),
    ("object_legal_hold_set", {"on": True}),
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
    client.create_bucket(Bucket=BUCKET, ObjectLockEnabledForBucket=True)
    client.create_bucket(Bucket=PLAIN)
    client.put_object(Bucket=BUCKET, Key="k", Body=b"x")
    client.put_object(Bucket=PLAIN, Key="k", Body=b"x")
    return client


@pytest.fixture
def hooks(monkeypatch):
    """Register botocore event handlers on every client the server creates.

    moto has no GetObjectRetention handler and doesn't enforce the governance bypass,
    so those paths are checked at the botocore level.
    """
    handlers: list[tuple[str, object]] = []
    original = Clients._client

    def patched(self, service, region, endpoint, config):
        client = original(self, service, region, endpoint, config)
        for event, handler in handlers:
            client.meta.events.register(event, handler, unique_id=f"test-{event}-{id(handler)}")
        return client

    monkeypatch.setattr(Clients, "_client", patched)
    return handlers


def _canned(parsed):
    """A ``before-call`` handler that answers the call with ``parsed`` instead of sending it."""

    def handler(**_):
        return SimpleNamespace(status_code=200, headers={}), parsed

    return handler


def _later(days=2):
    return (datetime.now(UTC) + timedelta(days=days)).replace(microsecond=0)


async def test_object_retention_set_governance(server, s3):
    until = _later()
    result = await call(
        server,
        "object_retention_set",
        bucket=BUCKET,
        key="k",
        mode="GOVERNANCE",
        retain_until=until.isoformat(),
        confirm=True,
    )
    assert result["mode"] == "GOVERNANCE"
    assert "note" not in result
    head = s3.head_object(Bucket=BUCKET, Key="k")
    assert head["ObjectLockMode"] == "GOVERNANCE"
    assert head["ObjectLockRetainUntilDate"] == until


async def test_object_retention_set_compliance_warns_it_cannot_be_shortened(server, s3):
    result = await call(
        server,
        "object_retention_set",
        bucket=BUCKET,
        key="k",
        mode="COMPLIANCE",
        retain_until=_later().isoformat(),
        confirm=True,
    )
    assert "can't be shortened" in result["note"]


async def test_object_retention_set_sends_bypass_governance_header(server, s3, hooks):
    sent = []
    hooks.append(
        (
            "before-send.s3.PutObjectRetention",
            lambda request, **_: sent.append(dict(request.headers)),
        )
    )
    for bypass in (False, True):
        await call(
            server,
            "object_retention_set",
            bucket=BUCKET,
            key="k",
            mode="GOVERNANCE",
            retain_until=_later().isoformat(),
            bypass_governance=bypass,
            confirm=True,
        )
    assert "x-amz-bypass-governance-retention" not in sent[0]
    assert sent[1]["x-amz-bypass-governance-retention"] == b"true"


async def test_object_retention_set_rejects_bad_mode_and_time(server, s3):
    with pytest.raises(ToolError):
        await call(
            server,
            "object_retention_set",
            bucket=BUCKET,
            key="k",
            mode="FOREVER",
            retain_until=_later().isoformat(),
            confirm=True,
        )
    with pytest.raises(ToolError, match="retain_until"):
        await call(
            server,
            "object_retention_set",
            bucket=BUCKET,
            key="k",
            mode="GOVERNANCE",
            retain_until="next week",
            confirm=True,
        )


async def test_object_retention_set_on_bucket_without_object_lock_is_explained(server, s3):
    with pytest.raises(ToolError, match="PutObjectRetention failed"):
        await call(
            server,
            "object_retention_set",
            bucket=PLAIN,
            key="k",
            mode="GOVERNANCE",
            retain_until=_later().isoformat(),
            confirm=True,
        )


async def test_object_retention_get_returns_mode_and_date(server, s3, hooks):
    until = _later()
    hooks.append(
        (
            "before-call.s3.GetObjectRetention",
            _canned({"Retention": {"Mode": "GOVERNANCE", "RetainUntilDate": until}}),
        )
    )
    result = await call(server, "object_retention_get", bucket=BUCKET, key="k")
    assert result == {
        "configured": True,
        "mode": "GOVERNANCE",
        "retain_until": until.isoformat(),
    }


async def test_object_retention_get_without_retention_is_not_configured(server, s3, hooks):
    error = {
        "Error": {"Code": "NoSuchObjectLockConfiguration", "Message": "none"},
        "ResponseMetadata": {"HTTPStatusCode": 404},
    }
    hooks.append(
        (
            "before-call.s3.GetObjectRetention",
            lambda **_: (SimpleNamespace(status_code=404, headers={}), error),
        )
    )
    assert await call(server, "object_retention_get", bucket=BUCKET, key="k") == {
        "configured": False
    }


async def test_object_retention_get_other_errors_are_explained(server, s3, hooks):
    error = {
        "Error": {"Code": "AccessDenied", "Message": "denied"},
        "ResponseMetadata": {"HTTPStatusCode": 403},
    }
    hooks.append(
        (
            "before-call.s3.GetObjectRetention",
            lambda **_: (SimpleNamespace(status_code=403, headers={}), error),
        )
    )
    with pytest.raises(ToolError, match="GetObjectRetention failed: AccessDenied"):
        await call(server, "object_retention_get", bucket=BUCKET, key="k")


async def test_object_legal_hold_set_and_get(server, s3):
    assert await call(server, "object_legal_hold_get", bucket=BUCKET, key="k") == {
        "configured": False
    }
    on = await call(server, "object_legal_hold_set", bucket=BUCKET, key="k", on=True, confirm=True)
    assert on["status"] == "ON"
    assert await call(server, "object_legal_hold_get", bucket=BUCKET, key="k") == {
        "configured": True,
        "status": "ON",
        "on": True,
    }
    await call(server, "object_legal_hold_set", bucket=BUCKET, key="k", on=False, confirm=True)
    result = await call(server, "object_legal_hold_get", bucket=BUCKET, key="k")
    assert result["on"] is False
    assert s3.head_object(Bucket=BUCKET, Key="k")["ObjectLockLegalHoldStatus"] == "OFF"


@pytest.mark.parametrize(("name", "arguments"), LOCK_CALLS, ids=[c[0] for c in LOCK_CALLS])
async def test_lock_set_tools_refuse_without_confirm(server, s3, name, arguments):
    with pytest.raises(ToolError, match="confirm=true"):
        await call(server, name, bucket=BUCKET, key="k", **arguments)
    assert "ObjectLockMode" not in s3.head_object(Bucket=BUCKET, Key="k")
    assert "ObjectLockLegalHoldStatus" not in s3.head_object(Bucket=BUCKET, Key="k")


@pytest.mark.parametrize(("name", "arguments"), LOCK_CALLS, ids=[c[0] for c in LOCK_CALLS])
async def test_lock_set_tools_refuse_in_read_only_mode(read_only_server, s3, name, arguments):
    with pytest.raises(ToolError, match="read-only"):
        await call(read_only_server, name, bucket=BUCKET, key="k", confirm=True, **arguments)

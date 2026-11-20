from urllib.parse import urlsplit
from xml.etree import ElementTree

import boto3
import httpx
import pytest
import respx

from tests.conftest import FAKE, FAKE_ACCESS_KEY, FAKE_SECRET_KEY, SECRETS
from tests.helpers import ToolError, call
from wasabi_mcp.config import Settings
from wasabi_mcp.server import build_server
from wasabi_mcp.tools import DESTRUCTIVE, READ

S3 = FAKE["s3"]

BUCKET_COMPLIANCE = b"""<?xml version="1.0" encoding="UTF-8"?>
<BucketComplianceConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Status>enabled</Status>
  <LockTime>2016-11-07T15:08:05Z</LockTime>
  <IsLocked>false</IsLocked>
  <RetentionDays>365</RetentionDays>
  <ConditionalHold>false</ConditionalHold>
  <DeleteAfterRetention>true</DeleteAfterRetention>
</BucketComplianceConfiguration>"""

OBJECT_COMPLIANCE = b"""<?xml version="1.0" encoding="UTF-8"?>
<ObjectComplianceConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <RetentionTime>2030-06-01T00:00:00Z</RetentionTime>
  <ConditionalHold>true</ConditionalHold>
  <LegalHold>false</LegalHold>
  <SHA256>14b4be3894e92166b508007b6c2e4fb6e88d3d0ad652c76475089a50ebe6e33b</SHA256>
</ObjectComplianceConfiguration>"""

COMPOSED_COMPLIANCE = b"""<ObjectComplianceConfiguration>
  <RetentionTime>2030-06-01T00:00:00Z</RetentionTime>
  <ConditionalHold>false</ConditionalHold>
  <LegalHold>true</LegalHold>
</ObjectComplianceConfiguration>"""


def _error(code: str, message: str, status: int = 400) -> httpx.Response:
    body = f"<Error><Code>{code}</Code><Message>{message}</Message></Error>"
    return httpx.Response(status, content=body.encode())


def _assert_signed(request: httpx.Request, region: str = "eu-central-1") -> None:
    authorization = request.headers["authorization"]
    assert authorization.startswith(f"AWS4-HMAC-SHA256 Credential={FAKE_ACCESS_KEY}/")
    assert f"/{region}/s3/aws4_request" in authorization
    assert request.headers["x-amz-content-sha256"] == "UNSIGNED-PAYLOAD"
    assert FAKE_SECRET_KEY not in authorization


def _fields(content: bytes) -> tuple[str, dict[str, str | None]]:
    root = ElementTree.fromstring(content)
    return root.tag, {child.tag: child.text for child in root}


async def test_tools_have_catalogue_kinds(rest_server):
    tools = {tool.name: tool for tool in await rest_server.list_tools()}
    assert tools["bucket_compliance_get"].annotations == READ
    assert tools["bucket_compliance_set"].annotations == DESTRUCTIVE
    assert tools["object_compliance_get"].annotations == READ
    assert tools["object_compliance_set"].annotations == DESTRUCTIVE
    assert "Wasabi Support" in tools["bucket_compliance_set"].description


# bucket_compliance_get


@respx.mock
async def test_bucket_compliance_get_returns_snake_case_settings(rest_server):
    route = respx.route(method="GET", host="s3.wasabi.test").mock(
        return_value=httpx.Response(200, content=BUCKET_COMPLIANCE)
    )
    result = await call(rest_server, "bucket_compliance_get", bucket="vault")
    request = route.calls.last.request
    assert request.method == "GET"
    assert str(request.url) == f"{S3}/vault?compliance"
    _assert_signed(request, "us-east-1")
    assert result == {
        "bucket": "vault",
        "status": "enabled",
        "lock_time": "2016-11-07T15:08:05Z",
        "is_locked": False,
        "retention_days": 365,
        "conditional_hold": False,
        "delete_after_retention": True,
    }


@respx.mock
async def test_bucket_compliance_get_signs_for_the_given_region(rest_server):
    route = respx.route(method="GET").mock(
        return_value=httpx.Response(200, content=BUCKET_COMPLIANCE)
    )
    await call(rest_server, "bucket_compliance_get", bucket="vault", region="eu-central-1")
    _assert_signed(route.calls.last.request)


@respx.mock
async def test_bucket_compliance_get_reports_wasabi_error(rest_server):
    respx.route(method="GET").mock(
        return_value=_error("NoSuchBucket", "The specified bucket does not exist", 404)
    )
    with pytest.raises(ToolError) as excinfo:
        await call(rest_server, "bucket_compliance_get", bucket="missing")
    assert str(excinfo.value).endswith(
        "GetBucketCompliance failed: NoSuchBucket: The specified bucket does not exist"
    )


# bucket_compliance_set


@respx.mock
async def test_bucket_compliance_set_puts_the_configuration(rest_server):
    route = respx.route(method="PUT", host="s3.wasabi.test").mock(return_value=httpx.Response(200))
    result = await call(
        rest_server,
        "bucket_compliance_set",
        bucket="vault",
        enabled=True,
        retention_days=365,
        conditional_hold=False,
        lock_time="off",
        delete_after_retention=True,
        region="eu-central-1",
        confirm=True,
    )
    request = route.calls.last.request
    assert request.method == "PUT"
    assert str(request.url) == f"{S3}/vault?compliance"
    _assert_signed(request)
    tag, fields = _fields(request.content)
    assert tag == "BucketComplianceConfiguration"
    assert fields == {
        "Status": "enabled",
        "RetentionDays": "365",
        "LockTime": "off",
        "ConditionalHold": "false",
        "DeleteAfterRetention": "true",
    }
    assert result == {
        "ok": True,
        "bucket": "vault",
        "region": "eu-central-1",
        "status": "enabled",
        "retention_days": 365,
        "lock_time": "off",
        "conditional_hold": False,
        "delete_after_retention": True,
    }


@respx.mock
async def test_bucket_compliance_set_reaches_the_real_regional_endpoint():
    settings = Settings(
        access_key_id=FAKE_ACCESS_KEY,
        secret_access_key=FAKE_SECRET_KEY,
        region="us-east-1",
        read_only=False,
    )
    route = respx.route(method="PUT").mock(return_value=httpx.Response(200))
    await call(
        build_server(settings),
        "bucket_compliance_set",
        bucket="vault",
        enabled=False,
        retention_days=0,
        region="eu-central-1",
        confirm=True,
    )
    request = route.calls.last.request
    assert str(request.url) == "https://s3.eu-central-1.wasabisys.com/vault?compliance"
    assert _fields(request.content)[1] == {"Status": "disabled", "RetentionDays": "0"}


@pytest.mark.parametrize(
    ("lock_time", "sent"),
    [
        ("now", "now"),
        ("OFF", "off"),
        ("2030-01-31T12:00:00Z", "2030-01-31T12:00:00Z"),
        ("2030-01-31T13:00:00+01:00", "2030-01-31T12:00:00Z"),
        ("2030-01-31", "2030-01-31T00:00:00Z"),
    ],
)
@respx.mock
async def test_bucket_compliance_set_accepts_lock_times(rest_server, lock_time, sent):
    route = respx.route(method="PUT").mock(return_value=httpx.Response(200))
    await call(
        rest_server,
        "bucket_compliance_set",
        bucket="vault",
        enabled=True,
        retention_days=30,
        lock_time=lock_time,
        region="us-east-1",
        confirm=True,
    )
    assert _fields(route.calls.last.request.content)[1]["LockTime"] == sent


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"retention_days": 30, "lock_time": "tomorrow"}, "lock_time must be an ISO 8601"),
        ({"retention_days": -1}, "retention_days must be a non-negative integer"),
    ],
)
@respx.mock
async def test_bucket_compliance_set_validates_before_sending(rest_server, arguments, message):
    route = respx.route().mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match=message):
        await call(
            rest_server,
            "bucket_compliance_set",
            bucket="vault",
            enabled=True,
            region="us-east-1",
            confirm=True,
            **arguments,
        )
    assert not route.called


@respx.mock
async def test_bucket_compliance_set_requires_retention_days(rest_server):
    route = respx.route().mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match="retention_days"):
        await call(
            rest_server,
            "bucket_compliance_set",
            bucket="vault",
            enabled=True,
            region="us-east-1",
            confirm=True,
        )
    assert not route.called


@respx.mock
async def test_bucket_compliance_set_refuses_without_confirm(rest_server):
    route = respx.route().mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match="PutBucketCompliance refused: this is destructive"):
        await call(
            rest_server,
            "bucket_compliance_set",
            bucket="vault",
            enabled=True,
            retention_days=30,
            region="us-east-1",
        )
    assert not route.called


@respx.mock
async def test_bucket_compliance_set_refuses_when_read_only(read_only_server):
    route = respx.route().mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match="PutBucketCompliance refused: the server is read-only"):
        await call(
            read_only_server,
            "bucket_compliance_set",
            bucket="vault",
            enabled=True,
            retention_days=30,
            region="us-east-1",
            confirm=True,
        )
    assert not route.called


@respx.mock
async def test_bucket_compliance_set_surfaces_compliance_settings_locked(rest_server):
    respx.route(method="PUT").mock(
        return_value=_error("ComplianceSettingsLocked", "The compliance settings are locked", 409)
    )
    with pytest.raises(ToolError) as excinfo:
        await call(
            rest_server,
            "bucket_compliance_set",
            bucket="vault",
            enabled=False,
            retention_days=0,
            region="us-east-1",
            confirm=True,
        )
    message = str(excinfo.value)
    assert "PutBucketCompliance failed: ComplianceSettingsLocked: " in message
    assert "The compliance settings are locked" in message
    assert "contact Wasabi Support" in message


# object_compliance_get


@respx.mock
async def test_object_compliance_get_returns_settings_and_sha256(rest_server):
    route = respx.route(method="GET", host="s3.wasabi.test").mock(
        return_value=httpx.Response(200, content=OBJECT_COMPLIANCE)
    )
    result = await call(rest_server, "object_compliance_get", bucket="vault", key="docs/a b.pdf")
    request = route.calls.last.request
    assert request.method == "GET"
    assert str(request.url) == f"{S3}/vault/docs/a%20b.pdf?compliance"
    _assert_signed(request, "us-east-1")
    assert result == {
        "bucket": "vault",
        "key": "docs/a b.pdf",
        "retention_time": "2030-06-01T00:00:00Z",
        "conditional_hold": True,
        "legal_hold": False,
        "sha256": "14b4be3894e92166b508007b6c2e4fb6e88d3d0ad652c76475089a50ebe6e33b",
    }


@respx.mock
async def test_object_compliance_get_has_no_sha256_for_composed_objects(rest_server):
    respx.route(method="GET").mock(return_value=httpx.Response(200, content=COMPOSED_COMPLIANCE))
    result = await call(rest_server, "object_compliance_get", bucket="vault", key="joined")
    assert result["sha256"] is None
    assert result["legal_hold"] is True


@respx.mock
async def test_object_compliance_get_reports_wasabi_error(rest_server):
    respx.route(method="GET").mock(
        return_value=_error("NoSuchKey", "The specified key does not exist.", 404)
    )
    with pytest.raises(ToolError, match="GetObjectCompliance failed: NoSuchKey: The specified"):
        await call(rest_server, "object_compliance_get", bucket="vault", key="gone")


# object_compliance_set


@respx.mock
async def test_object_compliance_set_releases_hold_and_sets_legal_hold(rest_server):
    route = respx.route(method="PUT", host="s3.wasabi.test").mock(return_value=httpx.Response(200))
    result = await call(
        rest_server,
        "object_compliance_set",
        bucket="vault",
        key="a.pdf",
        release_conditional_hold=True,
        legal_hold=True,
        region="eu-central-1",
        confirm=True,
    )
    request = route.calls.last.request
    assert route.call_count == 1
    assert request.method == "PUT"
    assert str(request.url) == f"{S3}/vault/a.pdf?compliance"
    _assert_signed(request)
    tag, fields = _fields(request.content)
    assert tag == "ObjectComplianceConfiguration"
    assert fields == {"ConditionalHold": "false", "LegalHold": "true"}
    assert result == {
        "ok": True,
        "bucket": "vault",
        "key": "a.pdf",
        "region": "eu-central-1",
        "conditional_hold": False,
        "legal_hold": True,
    }


@respx.mock
async def test_object_compliance_set_extends_retention_after_checking_current(rest_server):
    get = respx.route(method="GET").mock(
        return_value=httpx.Response(200, content=OBJECT_COMPLIANCE)
    )
    put = respx.route(method="PUT").mock(return_value=httpx.Response(200))
    result = await call(
        rest_server,
        "object_compliance_set",
        bucket="vault",
        key="a.pdf",
        retention_time="2031-01-01T00:00:00Z",
        region="eu-central-1",
        confirm=True,
    )
    assert str(get.calls.last.request.url) == f"{S3}/vault/a.pdf?compliance"
    _assert_signed(get.calls.last.request)
    assert _fields(put.calls.last.request.content)[1] == {"RetentionTime": "2031-01-01T00:00:00Z"}
    assert result["retention_time"] == "2031-01-01T00:00:00Z"


@respx.mock
async def test_object_compliance_set_refuses_a_shorter_retention_before_the_put(rest_server):
    get = respx.route(method="GET").mock(
        return_value=httpx.Response(200, content=OBJECT_COMPLIANCE)
    )
    put = respx.route(method="PUT").mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match="earlier than the current retention time"):
        await call(
            rest_server,
            "object_compliance_set",
            bucket="vault",
            key="a.pdf",
            retention_time="2030-05-31T23:59:59Z",
            region="us-east-1",
            confirm=True,
        )
    assert get.call_count == 1
    assert not put.called


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "give release_conditional_hold=true, legal_hold or retention_time"),
        ({"retention_time": "next year"}, "retention_time must be an ISO 8601"),
    ],
)
@respx.mock
async def test_object_compliance_set_validates_before_sending(rest_server, arguments, message):
    route = respx.route().mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match=message):
        await call(
            rest_server,
            "object_compliance_set",
            bucket="vault",
            key="a.pdf",
            region="us-east-1",
            confirm=True,
            **arguments,
        )
    assert not route.called


@respx.mock
async def test_object_compliance_set_refuses_without_confirm(rest_server):
    route = respx.route().mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match="PutObjectCompliance refused: this is destructive"):
        await call(
            rest_server,
            "object_compliance_set",
            bucket="vault",
            key="a.pdf",
            retention_time="2031-01-01T00:00:00Z",
            region="us-east-1",
        )
    assert not route.called


@respx.mock
async def test_object_compliance_set_refuses_when_read_only(read_only_server):
    route = respx.route().mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match="PutObjectCompliance refused: the server is read-only"):
        await call(
            read_only_server,
            "object_compliance_set",
            bucket="vault",
            key="a.pdf",
            legal_hold=False,
            region="us-east-1",
            confirm=True,
        )
    assert not route.called


@respx.mock
async def test_object_compliance_set_reports_wasabi_error(rest_server):
    respx.route(method="PUT").mock(
        return_value=_error("InvalidArgument", "Retention time is before the bucket policy")
    )
    with pytest.raises(ToolError) as excinfo:
        await call(
            rest_server,
            "object_compliance_set",
            bucket="vault",
            key="a.pdf",
            legal_hold=True,
            region="us-east-1",
            confirm=True,
        )
    assert str(excinfo.value).endswith(
        "PutObjectCompliance failed: InvalidArgument: Retention time is before the bucket policy"
    )


# Region lookup and secrets


@respx.mock
async def test_writes_use_the_bucket_region_from_get_bucket_location(settings, moto_url):
    boto3.client(
        "s3",
        region_name="eu-central-1",
        endpoint_url=moto_url,
        aws_access_key_id=FAKE_ACCESS_KEY,
        aws_secret_access_key=FAKE_SECRET_KEY,
    ).create_bucket(
        Bucket="eu-vault", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
    )
    route = respx.route(method="PUT", host=urlsplit(moto_url).hostname).mock(
        return_value=httpx.Response(200)
    )
    server = build_server(settings)
    await call(
        server,
        "bucket_compliance_set",
        bucket="eu-vault",
        enabled=True,
        retention_days=1,
        confirm=True,
    )
    await call(
        server, "object_compliance_set", bucket="eu-vault", key="k", legal_hold=True, confirm=True
    )
    assert route.call_count == 2
    for call_ in route.calls:
        _assert_signed(call_.request, "eu-central-1")


@respx.mock
async def test_results_never_contain_secrets(rest_server):
    respx.route(method="GET").mock(return_value=httpx.Response(200, content=BUCKET_COMPLIANCE))
    result = await call(rest_server, "bucket_compliance_get", bucket="vault")
    assert not any(secret in str(result) for secret in SECRETS)

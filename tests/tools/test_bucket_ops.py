import httpx
import pytest
import respx

from tests.conftest import FAKE
from tests.helpers import ToolError, call
from wasabi_mcp import rest
from wasabi_mcp.clients import Clients
from wasabi_mcp.tools import DESTRUCTIVE, READ, WRITE

KINDS = {
    "bucket_logging_get": READ,
    "bucket_logging_set": WRITE,
    "bucket_inventory_list": READ,
    "bucket_inventory_get": READ,
    "bucket_inventory_set": WRITE,
    "bucket_inventory_delete": DESTRUCTIVE,
}

SOURCE = "source-bucket"
LOGS = "logs-bucket"
INVENTORY_URL = f"{FAKE['s3']}/{SOURCE}?inventory&id=weekly"
CONFIG_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<InventoryConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Id>weekly</Id>
  <IsEnabled>true</IsEnabled>
  <Filter><Prefix>media/</Prefix></Filter>
  <Destination><S3BucketDestination>
    <Format>CSV</Format>
    <Bucket>arn:aws:s3:::reports-bucket</Bucket>
    <Prefix>inventory/</Prefix>
  </S3BucketDestination></Destination>
  <Schedule><Frequency>Weekly</Frequency></Schedule>
  <IncludedObjectVersions>All</IncludedObjectVersions>
  <OptionalFields><Field>Size</Field><Field>ETag</Field></OptionalFields>
</InventoryConfiguration>"""
CONFIG = {
    "id": "weekly",
    "enabled": True,
    "prefix": "media/",
    "destination_bucket": "reports-bucket",
    "destination_prefix": "inventory/",
    "destination_account_id": None,
    "format": "CSV",
    "frequency": "Weekly",
    "included_versions": "All",
    "optional_fields": ["Size", "ETag"],
}


def _error(code: str, message: str, status: int = 404) -> httpx.Response:
    body = f"<Error><Code>{code}</Code><Message>{message}</Message></Error>".encode()
    return httpx.Response(status, content=body)


@pytest.fixture
def s3(settings):
    client = Clients(settings).s3()
    client.create_bucket(Bucket=SOURCE)
    client.create_bucket(Bucket=LOGS, ACL="log-delivery-write")
    return client


async def test_tools_are_registered_with_their_kinds(server):
    tools = {tool.name: tool for tool in await server.list_tools()}
    for name, kind in KINDS.items():
        assert tools[name].annotations == kind, name
    assert "no SLA" in tools["bucket_logging_set"].description


# logging


async def test_bucket_logging_get_when_off(server, s3):
    assert await call(server, "bucket_logging_get", bucket=SOURCE) == {
        "bucket": SOURCE,
        "enabled": False,
        "target_bucket": None,
        "target_prefix": None,
    }


async def test_bucket_logging_set_on_and_off(server, s3):
    result = await call(
        server, "bucket_logging_set", bucket=SOURCE, target_bucket=LOGS, target_prefix="logs/"
    )
    assert result == {
        "ok": True,
        "bucket": SOURCE,
        "enabled": True,
        "target_bucket": LOGS,
        "target_prefix": "logs/",
    }
    got = await call(server, "bucket_logging_get", bucket=SOURCE)
    assert got["enabled"] is True
    assert (got["target_bucket"], got["target_prefix"]) == (LOGS, "logs/")
    await call(server, "bucket_logging_set", bucket=SOURCE, enabled=False)
    assert (await call(server, "bucket_logging_get", bucket=SOURCE))["enabled"] is False


async def test_bucket_logging_set_needs_a_target(server, s3):
    with pytest.raises(ToolError, match="target_bucket is required"):
        await call(server, "bucket_logging_set", bucket=SOURCE)


async def test_bucket_logging_set_bad_target_explains(server, s3):
    with pytest.raises(ToolError, match="InvalidTargetBucketForLogging: .*allow log delivery"):
        await call(server, "bucket_logging_set", bucket=SOURCE, target_bucket="no-such-bucket")


def test_logging_bucket_same_owner_gets_a_fix():
    from botocore.exceptions import ClientError

    from wasabi_mcp.tools import bucket_ops

    exc = ClientError(
        {"Error": {"Code": "LoggingBucketSameOwner", "Message": "Owner mismatch"}},
        "PutBucketLogging",
    )
    message = str(bucket_ops._fail(exc, "PutBucketLogging"))
    assert message.startswith("PutBucketLogging failed: LoggingBucketSameOwner: Owner mismatch")
    assert "same Wasabi account" in message


async def test_bucket_logging_set_refused_when_read_only(read_only_server, s3):
    with pytest.raises(ToolError, match="PutBucketLogging refused: the server is read-only"):
        await call(read_only_server, "bucket_logging_set", bucket=SOURCE, target_bucket=LOGS)
    assert "LoggingEnabled" not in s3.get_bucket_logging(Bucket=SOURCE)


async def test_bucket_logging_get_missing_bucket(server):
    with pytest.raises(ToolError, match="GetBucketLogging failed: NoSuchBucket: "):
        await call(server, "bucket_logging_get", bucket="no-such-bucket")


# inventory


@respx.mock
async def test_bucket_inventory_list(rest_server):
    page = (
        b'<ListInventoryConfigurationsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        + CONFIG_XML.split(b"?>", 1)[1]
        + b"<IsTruncated>true</IsTruncated><NextContinuationToken>tok2</NextContinuationToken>"
        b"</ListInventoryConfigurationsResult>"
    )
    route = respx.get(f"{FAKE['s3']}/{SOURCE}?inventory").mock(
        return_value=httpx.Response(200, content=page)
    )
    result = await call(rest_server, "bucket_inventory_list", bucket=SOURCE)
    assert result == {"items": [CONFIG], "next_token": "tok2", "truncated": True}
    assert route.called


@respx.mock
async def test_bucket_inventory_list_passes_the_continuation_token(rest_server):
    route = respx.get(f"{FAKE['s3']}/{SOURCE}?inventory&continuation-token=tok2").mock(
        return_value=httpx.Response(
            200,
            content=b"<ListInventoryConfigurationsResult><IsTruncated>false</IsTruncated>"
            b"</ListInventoryConfigurationsResult>",
        )
    )
    result = await call(
        rest_server, "bucket_inventory_list", bucket=SOURCE, continuation_token="tok2"
    )
    assert result == {"items": [], "next_token": None, "truncated": False}
    assert route.called


@respx.mock
async def test_bucket_inventory_get(rest_server):
    respx.get(INVENTORY_URL).mock(return_value=httpx.Response(200, content=CONFIG_XML))
    result = await call(rest_server, "bucket_inventory_get", bucket=SOURCE, inventory_id="weekly")
    assert result == {"configured": True, **CONFIG}


@respx.mock
async def test_bucket_inventory_get_unconfigured(rest_server):
    respx.get(INVENTORY_URL).mock(
        return_value=_error("NoSuchConfiguration", "The specified configuration does not exist.")
    )
    result = await call(rest_server, "bucket_inventory_get", bucket=SOURCE, inventory_id="weekly")
    assert result == {"configured": False}


@respx.mock
async def test_bucket_inventory_get_reports_other_errors(rest_server):
    respx.get(INVENTORY_URL).mock(return_value=_error("NoSuchBucket", "No such bucket"))
    with pytest.raises(ToolError, match="GetBucketInventoryConfiguration failed: NoSuchBucket"):
        await call(rest_server, "bucket_inventory_get", bucket=SOURCE, inventory_id="weekly")


@respx.mock
async def test_bucket_inventory_set_sends_the_configuration(rest_server):
    route = respx.put(INVENTORY_URL).mock(return_value=httpx.Response(200))
    result = await call(
        rest_server,
        "bucket_inventory_set",
        bucket=SOURCE,
        inventory_id="weekly",
        destination_bucket="reports-bucket",
        destination_prefix="inventory/",
        frequency="weekly",
        included_versions="all",
        prefix="media/",
        optional_fields=["Size", "etag"],
        region="us-east-1",
    )
    assert result == {"ok": True, "bucket": SOURCE, **CONFIG}
    request = route.calls.last.request
    sent = rest.parse_xml(request.content)
    assert sent.tag == "InventoryConfiguration"
    assert sent.findtext("Destination/S3BucketDestination/Bucket") == "arn:aws:s3:::reports-bucket"
    assert sent.findtext("Destination/S3BucketDestination/Format") == "CSV"
    assert sent.findtext("Schedule/Frequency") == "Weekly"
    assert [f.text for f in sent.findall("OptionalFields/Field")] == ["Size", "ETag"]
    assert request.headers["content-type"] == "application/xml"


@respx.mock
async def test_bucket_inventory_set_reports_wasabi_error(rest_server):
    respx.put(INVENTORY_URL).mock(
        return_value=_error("InvalidArgument", "Destination is invalid", status=400)
    )
    with pytest.raises(ToolError, match="PutBucketInventoryConfiguration failed: InvalidArgument"):
        await call(
            rest_server,
            "bucket_inventory_set",
            bucket=SOURCE,
            inventory_id="weekly",
            destination_bucket="reports-bucket",
            region="us-east-1",
        )


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"optional_fields": ["Owner"]}, "optional field must be one of Size"),
        ({"output_format": "Parquet"}, "CSV format only"),
        ({"frequency": "Monthly"}, "frequency must be one of Daily, Weekly"),
        ({"included_versions": "Latest"}, "included_versions must be one of Current, All"),
        ({"inventory_id": " "}, "inventory_id must not be empty"),
    ],
)
@respx.mock
async def test_bucket_inventory_set_validates_before_calling(rest_server, arguments, message):
    route = respx.route(host="s3.wasabi.test").mock(return_value=httpx.Response(200))
    base = {"inventory_id": "weekly", "destination_bucket": "reports-bucket", "region": "x"}
    with pytest.raises(ToolError, match=message):
        await call(rest_server, "bucket_inventory_set", bucket=SOURCE, **{**base, **arguments})
    assert not route.called


@respx.mock
async def test_bucket_inventory_set_refused_when_read_only(read_only_server):
    route = respx.route().mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match="PutBucketInventoryConfiguration refused: the server"):
        await call(
            read_only_server,
            "bucket_inventory_set",
            bucket=SOURCE,
            inventory_id="weekly",
            destination_bucket="reports-bucket",
        )
    assert not route.called


@respx.mock
async def test_bucket_inventory_delete(rest_server):
    route = respx.delete(INVENTORY_URL).mock(return_value=httpx.Response(204))
    result = await call(
        rest_server,
        "bucket_inventory_delete",
        bucket=SOURCE,
        inventory_id="weekly",
        region="us-east-1",
        confirm=True,
    )
    assert result == {"ok": True, "bucket": SOURCE, "inventory_id": "weekly"}
    assert route.called


@respx.mock
async def test_bucket_inventory_delete_reports_wasabi_error(rest_server):
    respx.delete(INVENTORY_URL).mock(
        return_value=_error("NoSuchConfiguration", "The specified configuration does not exist.")
    )
    with pytest.raises(ToolError, match="DeleteBucketInventoryConfiguration failed: NoSuch"):
        await call(
            rest_server,
            "bucket_inventory_delete",
            bucket=SOURCE,
            inventory_id="weekly",
            region="us-east-1",
            confirm=True,
        )


@respx.mock
async def test_bucket_inventory_delete_needs_confirm(rest_server):
    route = respx.route(host="s3.wasabi.test").mock(return_value=httpx.Response(204))
    with pytest.raises(ToolError, match="DeleteBucketInventoryConfiguration refused: this is"):
        await call(
            rest_server, "bucket_inventory_delete", bucket=SOURCE, inventory_id="weekly", region="x"
        )
    assert not route.called


@respx.mock
async def test_bucket_inventory_delete_refused_when_read_only(read_only_server):
    route = respx.route().mock(return_value=httpx.Response(204))
    with pytest.raises(ToolError, match="DeleteBucketInventoryConfiguration refused: the server"):
        await call(
            read_only_server,
            "bucket_inventory_delete",
            bucket=SOURCE,
            inventory_id="weekly",
            confirm=True,
        )
    assert not route.called

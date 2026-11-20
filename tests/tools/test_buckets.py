import httpx
import pytest
import respx
from botocore.stub import Stubber
from mcp.server.mcpserver import MCPServer

from tests.conftest import FAKE
from tests.helpers import ToolError, call
from wasabi_mcp.clients import Clients
from wasabi_mcp.server import build_server
from wasabi_mcp.tools import DESTRUCTIVE, READ, WRITE, ToolContext, buckets

KINDS = {
    "bucket_list": READ,
    "bucket_get": READ,
    "bucket_create": WRITE,
    "bucket_delete": DESTRUCTIVE,
    "bucket_rename": DESTRUCTIVE,
    "bucket_versioning_get": READ,
    "bucket_versioning_set": WRITE,
    "bucket_tagging_get": READ,
    "bucket_tagging_set": WRITE,
    "bucket_tagging_delete": DESTRUCTIVE,
    "bucket_location_get": READ,
}


@pytest.fixture
def s3(settings):
    """A boto3 client on moto, for arranging and checking state behind the tools."""
    return Clients(settings).s3()


async def test_tools_are_registered_with_their_kinds(server):
    tools = {tool.name: tool for tool in await server.list_tools()}
    for name, kind in KINDS.items():
        assert tools[name].annotations == kind, name


# bucket_list


async def test_bucket_list_returns_names_and_dates(server, s3):
    s3.create_bucket(Bucket="alpha")
    s3.create_bucket(Bucket="beta")
    result = await call(server, "bucket_list")
    assert [item["name"] for item in result["items"]] == ["alpha", "beta"]
    assert all(item["creation_date"] for item in result["items"])
    assert result["next_token"] is None
    assert result["truncated"] is False


async def test_bucket_list_paginates(server, s3):
    for name in ("aa1", "aa2", "aa3"):
        s3.create_bucket(Bucket=name)
    first = await call(server, "bucket_list", max_items=2)
    assert [item["name"] for item in first["items"]] == ["aa1", "aa2"]
    assert first["truncated"] is True
    second = await call(server, "bucket_list", max_items=2, continuation_token=first["next_token"])
    assert [item["name"] for item in second["items"]] == ["aa3"]
    assert second["truncated"] is False


async def test_bucket_list_includes_regions(server, s3):
    s3.create_bucket(Bucket="east")
    s3.create_bucket(
        Bucket="central", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
    )
    result = await call(server, "bucket_list", include_region=True)
    assert {item["name"]: item["region"] for item in result["items"]} == {
        "central": "eu-central-1",
        "east": "us-east-1",
    }


async def test_bucket_list_rejects_too_many_items(server):
    with pytest.raises(ToolError, match="max_items must be between 1 and 1000"):
        await call(server, "bucket_list", max_items=1001)


# bucket_get


async def test_bucket_get_summarises_a_plain_bucket(server, s3):
    s3.create_bucket(Bucket="plain")
    assert await call(server, "bucket_get", bucket="plain") == {
        "bucket": "plain",
        "region": "us-east-1",
        "versioning": "Unversioned",
        "mfa_delete": "Disabled",
        "object_lock_enabled": False,
        "default_retention": None,
    }


async def test_bucket_get_reports_object_lock_and_retention(server, s3):
    s3.create_bucket(Bucket="locked", ObjectLockEnabledForBucket=True)
    s3.put_object_lock_configuration(
        Bucket="locked",
        ObjectLockConfiguration={
            "ObjectLockEnabled": "Enabled",
            "Rule": {"DefaultRetention": {"Mode": "GOVERNANCE", "Days": 30}},
        },
    )
    result = await call(server, "bucket_get", bucket="locked")
    assert result["versioning"] == "Enabled"
    assert result["object_lock_enabled"] is True
    assert result["default_retention"] == {"Mode": "GOVERNANCE", "Days": 30}


async def test_bucket_get_missing_bucket_says_no_such_bucket(server):
    with pytest.raises(ToolError, match="HeadBucket failed: NoSuchBucket: The bucket nope"):
        await call(server, "bucket_get", bucket="nope")


# bucket_create


async def test_bucket_create_in_default_region(server, s3):
    result = await call(server, "bucket_create", bucket="made")
    assert result == {
        "ok": True,
        "bucket": "made",
        "region": "us-east-1",
        "object_lock": False,
        "versioning": "Unversioned",
    }
    assert "made" in [b["Name"] for b in s3.list_buckets()["Buckets"]]


async def test_bucket_create_in_another_region(server, s3):
    await call(server, "bucket_create", bucket="eu-bucket", region="eu-central-1")
    assert s3.get_bucket_location(Bucket="eu-bucket")["LocationConstraint"] == "eu-central-1"


async def test_bucket_create_with_object_lock_enables_versioning(server, s3):
    result = await call(server, "bucket_create", bucket="vault", object_lock=True)
    assert result["object_lock"] is True
    assert result["versioning"] == "Enabled"
    lock = s3.get_object_lock_configuration(Bucket="vault")["ObjectLockConfiguration"]
    assert lock["ObjectLockEnabled"] == "Enabled"
    assert s3.get_bucket_versioning(Bucket="vault")["Status"] == "Enabled"


async def test_bucket_create_description_says_lock_is_irreversible(server):
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert "never be turned off" in tools["bucket_create"].description


async def test_bucket_create_refused_when_read_only(read_only_server, s3):
    with pytest.raises(ToolError, match="CreateBucket refused: the server is read-only"):
        await call(read_only_server, "bucket_create", bucket="nope")
    assert s3.list_buckets()["Buckets"] == []


async def test_bucket_create_invalid_name_reports_wasabi_error(server):
    with pytest.raises(ToolError, match="CreateBucket failed: InvalidBucketName: "):
        await call(server, "bucket_create", bucket="ab")


def test_too_many_buckets_error_gets_a_fix():
    from botocore.exceptions import ClientError

    exc = ClientError(
        {"Error": {"Code": "TooManyBuckets", "Message": "You have too many buckets"}},
        "CreateBucket",
    )
    message = str(buckets._fail(exc, "CreateBucket"))
    assert message.startswith("CreateBucket failed: TooManyBuckets: You have too many buckets")
    assert "1,000 buckets" in message


# bucket_delete


async def test_bucket_delete(server, s3):
    s3.create_bucket(Bucket="doomed")
    result = await call(server, "bucket_delete", bucket="doomed", confirm=True)
    assert result == {"ok": True, "bucket": "doomed", "region": "us-east-1", "forced": False}
    assert s3.list_buckets()["Buckets"] == []


async def test_bucket_delete_needs_confirm(server, s3):
    s3.create_bucket(Bucket="kept")
    with pytest.raises(ToolError, match="DeleteBucket refused: this is destructive"):
        await call(server, "bucket_delete", bucket="kept")
    assert [b["Name"] for b in s3.list_buckets()["Buckets"]] == ["kept"]


async def test_bucket_delete_refused_when_read_only(read_only_server, s3):
    s3.create_bucket(Bucket="kept")
    with pytest.raises(ToolError, match="DeleteBucket refused: the server is read-only"):
        await call(read_only_server, "bucket_delete", bucket="kept", confirm=True)
    assert [b["Name"] for b in s3.list_buckets()["Buckets"]] == ["kept"]


async def test_bucket_delete_non_empty_suggests_force(server, s3):
    s3.create_bucket(Bucket="full")
    s3.put_object(Bucket="full", Key="a.txt", Body=b"a")
    with pytest.raises(ToolError, match="DeleteBucket failed: BucketNotEmpty: .*force=true"):
        await call(server, "bucket_delete", bucket="full", confirm=True)


@respx.mock
async def test_bucket_delete_force_uses_wasabi_force_delete(rest_server):
    route = respx.delete(f"{FAKE['s3']}/full?force_delete=true").mock(
        return_value=httpx.Response(204)
    )
    result = await call(
        rest_server, "bucket_delete", bucket="full", force=True, region="us-east-1", confirm=True
    )
    assert result == {"ok": True, "bucket": "full", "region": "us-east-1", "forced": True}
    assert route.called
    assert "authorization" in route.calls.last.request.headers


@respx.mock
async def test_bucket_delete_force_reports_wasabi_error(rest_server):
    respx.delete(f"{FAKE['s3']}/locked?force_delete=true").mock(
        return_value=httpx.Response(
            409,
            content=b"<Error><Code>ObjectLocked</Code><Message>Objects are locked</Message>"
            b"</Error>",
        )
    )
    with pytest.raises(ToolError, match="DeleteBucket failed: ObjectLocked: Objects are locked"):
        await call(
            rest_server,
            "bucket_delete",
            bucket="locked",
            force=True,
            region="us-east-1",
            confirm=True,
        )


@respx.mock
async def test_bucket_delete_force_needs_confirm_and_sends_nothing(rest_server):
    route = respx.route(host="s3.wasabi.test").mock(return_value=httpx.Response(204))
    with pytest.raises(ToolError, match="confirm=true"):
        await call(rest_server, "bucket_delete", bucket="full", force=True, region="us-east-1")
    assert not route.called


# bucket_rename


@respx.mock
async def test_bucket_rename_sends_move_with_destination(rest_server):
    route = respx.route(method="MOVE", url=f"{FAKE['s3']}/old").mock(
        return_value=httpx.Response(200)
    )
    result = await call(
        rest_server,
        "bucket_rename",
        bucket="old",
        destination="new",
        region="us-east-1",
        confirm=True,
    )
    assert result == {"ok": True, "bucket": "new", "previous_name": "old", "region": "us-east-1"}
    request = route.calls.last.request
    assert request.headers["destination"] == "new"
    assert "destination;host" in request.headers["authorization"]


@respx.mock
async def test_bucket_rename_reports_wasabi_error(rest_server):
    respx.route(method="MOVE", url=f"{FAKE['s3']}/old").mock(
        return_value=httpx.Response(
            403, content=b"<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>"
        )
    )
    with pytest.raises(ToolError, match="RenameBucket failed: AccessDenied: Access Denied"):
        await call(
            rest_server,
            "bucket_rename",
            bucket="old",
            destination="new",
            region="us-east-1",
            confirm=True,
        )


@respx.mock
async def test_bucket_rename_needs_confirm(rest_server):
    route = respx.route(host="s3.wasabi.test").mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match="RenameBucket refused: this is destructive"):
        await call(rest_server, "bucket_rename", bucket="old", destination="new", region="x")
    assert not route.called


async def test_bucket_rename_refused_when_read_only(read_only_server):
    with pytest.raises(ToolError, match="RenameBucket refused: the server is read-only"):
        await call(read_only_server, "bucket_rename", bucket="old", destination="new", confirm=True)


# versioning


async def test_bucket_versioning_get_and_set(server, s3):
    s3.create_bucket(Bucket="vers")
    assert (await call(server, "bucket_versioning_get", bucket="vers"))["status"] == "Unversioned"
    assert await call(server, "bucket_versioning_set", bucket="vers", enabled=True) == {
        "ok": True,
        "bucket": "vers",
        "status": "Enabled",
    }
    assert await call(server, "bucket_versioning_get", bucket="vers") == {
        "bucket": "vers",
        "status": "Enabled",
        "mfa_delete": "Disabled",
    }
    await call(server, "bucket_versioning_set", bucket="vers", enabled=False)
    assert (await call(server, "bucket_versioning_get", bucket="vers"))["status"] == "Suspended"


async def test_bucket_versioning_set_refused_when_read_only(read_only_server, s3):
    s3.create_bucket(Bucket="vers")
    with pytest.raises(ToolError, match="PutBucketVersioning refused: the server is read-only"):
        await call(read_only_server, "bucket_versioning_set", bucket="vers", enabled=True)
    assert "Status" not in s3.get_bucket_versioning(Bucket="vers")


async def test_bucket_versioning_get_missing_bucket_error_format(server):
    with pytest.raises(ToolError) as info:
        await call(server, "bucket_versioning_get", bucket="missing")
    message = str(info.value)
    assert "GetBucketVersioning failed: NoSuchBucket: " in message
    assert "Traceback" not in message


# tagging


async def test_bucket_tagging_get_unconfigured(server, s3):
    s3.create_bucket(Bucket="tags")
    assert await call(server, "bucket_tagging_get", bucket="tags") == {"configured": False}


@pytest.mark.parametrize("drop_tag_set", [False, True], ids=["empty-tag-set", "no-tag-set"])
async def test_bucket_tagging_get_empty_tag_set_is_unconfigured(
    settings, monkeypatch, drop_tag_set
):
    # Wasabi answers GetBucketTagging on an untagged bucket with an empty TagSet, not
    # NoSuchTagSet; moto only models the AWS error, so stub Wasabi's reply.
    client = Clients(settings).s3()
    monkeypatch.setattr(Clients, "s3", lambda self, region=None: client)
    if drop_tag_set:
        # Stubber insists on TagSet (a required field), so remove it after parsing.
        client.meta.events.register(
            "after-call.s3.GetBucketTagging", lambda parsed, **_: parsed.pop("TagSet", None)
        )
    with Stubber(client) as stub:
        stub.add_response("get_bucket_tagging", {"TagSet": []}, {"Bucket": "tags"})
        server = build_server(settings)
        assert await call(server, "bucket_tagging_get", bucket="tags") == {"configured": False}
        stub.assert_no_pending_responses()


async def test_bucket_tagging_set_get_and_delete(server, s3):
    s3.create_bucket(Bucket="tags")
    tags = {"env": "dev", "team": "data"}
    assert await call(server, "bucket_tagging_set", bucket="tags", tags=tags) == {
        "ok": True,
        "bucket": "tags",
        "tags": tags,
    }
    assert await call(server, "bucket_tagging_get", bucket="tags") == {
        "configured": True,
        "bucket": "tags",
        "tags": tags,
    }
    assert await call(server, "bucket_tagging_delete", bucket="tags", confirm=True) == {
        "ok": True,
        "bucket": "tags",
    }
    assert await call(server, "bucket_tagging_get", bucket="tags") == {"configured": False}


async def test_bucket_tagging_set_refused_when_read_only(read_only_server, s3):
    s3.create_bucket(Bucket="tags")
    with pytest.raises(ToolError, match="PutBucketTagging refused: the server is read-only"):
        await call(read_only_server, "bucket_tagging_set", bucket="tags", tags={"a": "b"})
    assert await call(read_only_server, "bucket_tagging_get", bucket="tags") == {
        "configured": False
    }


async def test_bucket_tagging_delete_needs_confirm(server, s3):
    s3.create_bucket(Bucket="tags")
    s3.put_bucket_tagging(Bucket="tags", Tagging={"TagSet": [{"Key": "a", "Value": "b"}]})
    with pytest.raises(ToolError, match="DeleteBucketTagging refused: this is destructive"):
        await call(server, "bucket_tagging_delete", bucket="tags")
    assert s3.get_bucket_tagging(Bucket="tags")["TagSet"] == [{"Key": "a", "Value": "b"}]


async def test_bucket_tagging_delete_refused_when_read_only(read_only_server, s3):
    s3.create_bucket(Bucket="tags")
    s3.put_bucket_tagging(Bucket="tags", Tagging={"TagSet": [{"Key": "a", "Value": "b"}]})
    with pytest.raises(ToolError, match="DeleteBucketTagging refused: the server is read-only"):
        await call(read_only_server, "bucket_tagging_delete", bucket="tags", confirm=True)
    assert s3.get_bucket_tagging(Bucket="tags")["TagSet"] == [{"Key": "a", "Value": "b"}]


async def test_bucket_tagging_set_missing_bucket(server):
    with pytest.raises(ToolError, match="GetBucketLocation failed: NoSuchBucket: "):
        await call(server, "bucket_tagging_set", bucket="missing", tags={"a": "b"})


# location and region resolution


async def test_bucket_location_get(server, s3, settings):
    s3.create_bucket(
        Bucket="eu-bucket", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
    )
    s3.create_bucket(Bucket="us-bucket")
    assert await call(server, "bucket_location_get", bucket="eu-bucket") == {
        "bucket": "eu-bucket",
        "region": "eu-central-1",
        "endpoint": settings.s3_endpoint_for("eu-central-1"),
    }
    assert (await call(server, "bucket_location_get", bucket="us-bucket"))["region"] == "us-east-1"


async def test_bucket_writes_resolve_the_region_once_and_cache_it(settings, s3):
    s3.create_bucket(
        Bucket="eu-bucket", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
    )
    clients = Clients(settings)
    server = MCPServer("test")
    buckets.register(server, ToolContext(settings=settings, clients=clients))
    lookups = []
    clients.s3().meta.events.register(
        "before-call.s3.GetBucketLocation", lambda **kwargs: lookups.append(1)
    )
    await call(server, "bucket_tagging_set", bucket="eu-bucket", tags={"a": "1"})
    await call(server, "bucket_versioning_set", bucket="eu-bucket", enabled=True)
    assert len(lookups) == 1
    assert ("s3", "eu-central-1") in clients._cache
    await call(
        server, "bucket_tagging_set", bucket="eu-bucket", tags={"a": "2"}, region="eu-central-1"
    )
    assert len(lookups) == 1

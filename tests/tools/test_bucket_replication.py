import httpx
import pytest
import respx
from mcp.server.mcpserver import MCPServer

from tests.conftest import FAKE
from tests.helpers import ToolError, call
from wasabi_mcp.clients import Clients
from wasabi_mcp.tools import DESTRUCTIVE, READ, WRITE, ToolContext, bucket_replication

KINDS = {
    "bucket_replication_get": READ,
    "bucket_replication_set": DESTRUCTIVE,
    "bucket_replication_delete": DESTRUCTIVE,
    "bucket_replication_backfill": WRITE,
}

SOURCE = "source-bucket"
TARGET = "target-bucket"
BACKFILL_URL = f"{FAKE['s3']}/{SOURCE}?manifestCreateAndProcess"


@pytest.fixture
def s3(settings):
    client = Clients(settings).s3()
    for name in (SOURCE, TARGET):
        client.create_bucket(Bucket=name)
        client.put_bucket_versioning(Bucket=name, VersioningConfiguration={"Status": "Enabled"})
    return client


def _configured(s3) -> bool:
    try:
        s3.get_bucket_replication(Bucket=SOURCE)
    except s3.exceptions.ClientError:
        return False
    return True


def _recording_server(settings):
    """A server that records the PutBucketReplication parameters it sends."""
    clients = Clients(settings)
    server = MCPServer("test")
    bucket_replication.register(server, ToolContext(settings=settings, clients=clients))
    sent = []
    clients.s3().meta.events.register(
        "provide-client-params.s3.PutBucketReplication",
        lambda params, **kwargs: sent.append(params),
    )
    return server, sent


async def test_tools_are_registered_with_their_kinds(server):
    tools = {tool.name: tool for tool in await server.list_tools()}
    for name, kind in KINDS.items():
        assert tools[name].annotations == kind, name
    assert "same versioning state" in tools["bucket_replication_set"].description


async def test_bucket_replication_get_unconfigured(server, s3):
    assert await call(server, "bucket_replication_get", bucket=SOURCE) == {"configured": False}


async def test_bucket_replication_set_get_and_delete(server, s3):
    """Scenario S10 (configuration part): replicate the whole bucket to another."""
    result = await call(
        server,
        "bucket_replication_set",
        bucket=SOURCE,
        destination_bucket=TARGET,
        delete_marker_replication=True,
        confirm=True,
    )
    assert result == {
        "ok": True,
        "bucket": SOURCE,
        "role": "",
        "rules": [
            {
                "ID": "rule-1",
                "Priority": 1,
                "Status": "Enabled",
                "Filter": {"Prefix": ""},
                "DeleteMarkerReplication": {"Status": "Enabled"},
                "Destination": {"Bucket": f"arn:aws:s3:::{TARGET}"},
            }
        ],
    }
    got = await call(server, "bucket_replication_get", bucket=SOURCE)
    assert got["configured"] is True
    [rule] = got["rules"]
    assert rule["Destination"]["Bucket"] == f"arn:aws:s3:::{TARGET}"
    assert await call(server, "bucket_replication_delete", bucket=SOURCE, confirm=True) == {
        "ok": True,
        "bucket": SOURCE,
    }
    assert await call(server, "bucket_replication_get", bucket=SOURCE) == {"configured": False}


async def test_bucket_replication_set_builds_filters_and_cross_account_destination(settings, s3):
    server, sent = _recording_server(settings)
    rules = [
        {"id": "media", "prefix": "media/"},
        {"id": "tagged", "tags": {"replicate": "yes"}, "enabled": False},
        {"id": "both", "prefix": "docs/", "tags": {"a": "1"}, "delete_marker_replication": True},
    ]
    await call(
        server,
        "bucket_replication_set",
        bucket=SOURCE,
        destination_bucket=f"arn:aws:s3:::{TARGET}",
        rules=rules,
        role="arn:aws:iam::100000000000:role/replicator",
        destination_account="200000000000",
        owner_translation=True,
        confirm=True,
    )
    configuration = sent[0]["ReplicationConfiguration"]
    assert configuration["Role"] == "arn:aws:iam::100000000000:role/replicator"
    media, tagged, both = configuration["Rules"]
    assert media["Filter"] == {"Prefix": "media/"}
    assert tagged["Filter"] == {"Tag": {"Key": "replicate", "Value": "yes"}}
    assert tagged["Status"] == "Disabled"
    assert both["Filter"] == {"And": {"Prefix": "docs/", "Tags": [{"Key": "a", "Value": "1"}]}}
    assert both["DeleteMarkerReplication"] == {"Status": "Enabled"}
    assert [rule["Priority"] for rule in configuration["Rules"]] == [1, 2, 3]
    assert media["Destination"] == {
        "Bucket": f"arn:aws:s3:::{TARGET}",
        "Account": "200000000000",
        "AccessControlTranslation": {"Owner": "Destination"},
    }


async def test_bucket_replication_set_explains_a_versioning_mismatch(server, s3):
    s3.put_bucket_versioning(Bucket=TARGET, VersioningConfiguration={"Status": "Suspended"})
    with pytest.raises(ToolError, match="source-bucket versioning is Enabled but target-bucket is"):
        await call(
            server, "bucket_replication_set", bucket=SOURCE, destination_bucket=TARGET, confirm=True
        )
    assert not _configured(s3)


async def test_bucket_replication_set_on_unversioned_buckets_reports_wasabi_error(server, s3):
    s3.create_bucket(Bucket="plain-one")
    s3.create_bucket(Bucket="plain-two")
    with pytest.raises(ToolError, match="PutBucketReplication failed: InvalidRequest: Versioning"):
        await call(
            server,
            "bucket_replication_set",
            bucket="plain-one",
            destination_bucket="plain-two",
            confirm=True,
        )


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"owner_translation": True, "role": "arn:r"}, "owner_translation needs destination"),
        ({"destination_account": "200000000000"}, "Cross-account replication needs role"),
        ({"rules": [{"id": "x", "suffix": ".mp4"}]}, "unknown fields"),
    ],
)
async def test_bucket_replication_set_validates_before_calling(server, s3, arguments, message):
    with pytest.raises(ToolError, match=message):
        await call(
            server,
            "bucket_replication_set",
            bucket=SOURCE,
            destination_bucket=TARGET,
            confirm=True,
            **arguments,
        )
    assert not _configured(s3)


async def test_bucket_replication_set_needs_confirm(server, s3):
    with pytest.raises(ToolError, match="PutBucketReplication refused: this is destructive"):
        await call(server, "bucket_replication_set", bucket=SOURCE, destination_bucket=TARGET)
    assert not _configured(s3)


async def test_bucket_replication_set_refused_when_read_only(read_only_server, s3):
    with pytest.raises(ToolError, match="PutBucketReplication refused: the server is read-only"):
        await call(
            read_only_server,
            "bucket_replication_set",
            bucket=SOURCE,
            destination_bucket=TARGET,
            confirm=True,
        )
    assert not _configured(s3)


async def test_bucket_replication_delete_needs_confirm(server, s3):
    await call(
        server, "bucket_replication_set", bucket=SOURCE, destination_bucket=TARGET, confirm=True
    )
    with pytest.raises(ToolError, match="DeleteBucketReplication refused: this is destructive"):
        await call(server, "bucket_replication_delete", bucket=SOURCE)
    assert _configured(s3)


async def test_bucket_replication_delete_refused_when_read_only(server, read_only_server, s3):
    await call(
        server, "bucket_replication_set", bucket=SOURCE, destination_bucket=TARGET, confirm=True
    )
    with pytest.raises(ToolError, match="DeleteBucketReplication refused: the server is read-only"):
        await call(read_only_server, "bucket_replication_delete", bucket=SOURCE, confirm=True)
    assert _configured(s3)


async def test_bucket_replication_get_missing_bucket(server):
    with pytest.raises(ToolError, match="GetBucketReplication failed: NoSuchBucket: "):
        await call(server, "bucket_replication_get", bucket="no-such-bucket")


# backfill


@respx.mock
async def test_bucket_replication_backfill_returns_the_job_id_from_xml(rest_server):
    route = respx.put(BACKFILL_URL).mock(
        return_value=httpx.Response(200, content=b"<Result><JobId>job-42</JobId></Result>")
    )
    result = await call(
        rest_server, "bucket_replication_backfill", bucket=SOURCE, region="us-east-1"
    )
    assert result == {"ok": True, "bucket": SOURCE, "job_id": "job-42"}
    assert "authorization" in route.calls.last.request.headers


@respx.mock
async def test_bucket_replication_backfill_returns_the_job_id_from_json(rest_server):
    respx.put(BACKFILL_URL).mock(return_value=httpx.Response(200, json={"jobId": "job-7"}))
    result = await call(
        rest_server, "bucket_replication_backfill", bucket=SOURCE, region="us-east-1"
    )
    assert result["job_id"] == "job-7"


@respx.mock
async def test_bucket_replication_backfill_reports_wasabi_error(rest_server):
    respx.put(BACKFILL_URL).mock(
        return_value=httpx.Response(
            400,
            content=b"<Error><Code>InvalidRequest</Code>"
            b"<Message>No replication configuration</Message></Error>",
        )
    )
    with pytest.raises(ToolError, match="ReplicationBackfill failed: InvalidRequest: No repl"):
        await call(rest_server, "bucket_replication_backfill", bucket=SOURCE, region="us-east-1")


@respx.mock
async def test_bucket_replication_backfill_refused_when_read_only(read_only_server):
    route = respx.route().mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match="ReplicationBackfill refused: the server is read-only"):
        await call(read_only_server, "bucket_replication_backfill", bucket=SOURCE)
    assert not route.called


async def test_list_tools_includes_every_bucket_configuration_tool(server):
    """All 40 tools of this ticket are registered with the right kinds."""
    from tests.tools import (
        test_access,
        test_bucket_ops,
        test_bucket_rules,
        test_buckets,
        test_object_lock,
    )

    expected = {
        **test_buckets.KINDS,
        **test_access.KINDS,
        **test_bucket_rules.KINDS,
        **test_object_lock.KINDS,
        **test_bucket_ops.KINDS,
        **KINDS,
    }
    assert len(expected) == 40
    tools = {tool.name: tool.annotations for tool in await server.list_tools()}
    assert {name: tools.get(name) for name in expected} == expected

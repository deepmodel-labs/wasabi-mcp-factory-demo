import json

import pytest
from mcp.server.mcpserver import MCPServer

from tests.helpers import ToolError, call
from wasabi_mcp.clients import Clients
from wasabi_mcp.tools import DESTRUCTIVE, READ, ToolContext, access

KINDS = {
    "bucket_policy_get": READ,
    "bucket_policy_set": DESTRUCTIVE,
    "bucket_policy_delete": DESTRUCTIVE,
    "bucket_acl_get": READ,
    "bucket_acl_set": DESTRUCTIVE,
    "object_acl_get": READ,
    "object_acl_set": DESTRUCTIVE,
}

BUCKET = "policy-bucket"
POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"AWS": "arn:aws:iam::100000000000:user/reader"},
            "Action": "s3:GetObject",
            "Resource": f"arn:aws:s3:::{BUCKET}/*",
        }
    ],
}
ALL_USERS = "http://acs.amazonaws.com/groups/global/AllUsers"


@pytest.fixture
def s3(settings):
    client = Clients(settings).s3()
    client.create_bucket(Bucket=BUCKET)
    return client


def _grant_permissions(grants, uri):
    return {g["Permission"] for g in grants if g["Grantee"].get("URI") == uri}


async def test_tools_are_registered_with_their_kinds(server):
    tools = {tool.name: tool for tool in await server.list_tools()}
    for name, kind in KINDS.items():
        assert tools[name].annotations == kind, name
    assert "Wasabi" in tools["bucket_policy_set"].description
    assert "2023-03-13" in tools["bucket_acl_set"].description


# bucket policy


async def test_bucket_policy_get_unconfigured(server, s3):
    assert await call(server, "bucket_policy_get", bucket=BUCKET) == {"configured": False}


async def test_bucket_policy_set_get_and_delete(server, s3):
    result = await call(server, "bucket_policy_set", bucket=BUCKET, policy=POLICY, confirm=True)
    assert result == {"ok": True, "bucket": BUCKET, "policy": POLICY}
    assert await call(server, "bucket_policy_get", bucket=BUCKET) == {
        "configured": True,
        "bucket": BUCKET,
        "policy": POLICY,
    }
    assert await call(server, "bucket_policy_delete", bucket=BUCKET, confirm=True) == {
        "ok": True,
        "bucket": BUCKET,
    }
    assert await call(server, "bucket_policy_get", bucket=BUCKET) == {"configured": False}


async def test_bucket_policy_set_accepts_a_json_string(server, s3):
    await call(server, "bucket_policy_set", bucket=BUCKET, policy=json.dumps(POLICY), confirm=True)
    assert json.loads(s3.get_bucket_policy(Bucket=BUCKET)["Policy"]) == POLICY


async def test_bucket_policy_set_rejects_invalid_json_before_calling(server, s3):
    with pytest.raises(ToolError, match="policy isn't valid JSON"):
        await call(server, "bucket_policy_set", bucket=BUCKET, policy="{nope", confirm=True)
    with pytest.raises(ToolError, match="policy has no Statement"):
        await call(server, "bucket_policy_set", bucket=BUCKET, policy={"a": 1}, confirm=True)
    assert await call(server, "bucket_policy_get", bucket=BUCKET) == {"configured": False}


async def test_bucket_policy_set_needs_confirm(server, s3):
    with pytest.raises(ToolError, match="PutBucketPolicy refused: this is destructive"):
        await call(server, "bucket_policy_set", bucket=BUCKET, policy=POLICY)
    assert await call(server, "bucket_policy_get", bucket=BUCKET) == {"configured": False}


async def test_bucket_policy_set_refused_when_read_only(read_only_server, s3):
    with pytest.raises(ToolError, match="PutBucketPolicy refused: the server is read-only"):
        await call(
            read_only_server, "bucket_policy_set", bucket=BUCKET, policy=POLICY, confirm=True
        )
    assert await call(read_only_server, "bucket_policy_get", bucket=BUCKET) == {"configured": False}


async def test_bucket_policy_delete_needs_confirm(server, s3):
    s3.put_bucket_policy(Bucket=BUCKET, Policy=json.dumps(POLICY))
    with pytest.raises(ToolError, match="DeleteBucketPolicy refused: this is destructive"):
        await call(server, "bucket_policy_delete", bucket=BUCKET)
    assert json.loads(s3.get_bucket_policy(Bucket=BUCKET)["Policy"]) == POLICY


async def test_bucket_policy_delete_refused_when_read_only(read_only_server, s3):
    s3.put_bucket_policy(Bucket=BUCKET, Policy=json.dumps(POLICY))
    with pytest.raises(ToolError, match="DeleteBucketPolicy refused: the server is read-only"):
        await call(read_only_server, "bucket_policy_delete", bucket=BUCKET, confirm=True)
    assert json.loads(s3.get_bucket_policy(Bucket=BUCKET)["Policy"]) == POLICY


async def test_bucket_policy_get_missing_bucket(server):
    with pytest.raises(ToolError, match="GetBucketPolicy failed: NoSuchBucket: "):
        await call(server, "bucket_policy_get", bucket="no-such-bucket")


# bucket ACL


async def test_bucket_acl_get_shows_owner_and_grants(server, s3):
    result = await call(server, "bucket_acl_get", bucket=BUCKET)
    assert result["bucket"] == BUCKET
    assert result["owner"]["ID"]
    assert [g["Permission"] for g in result["grants"]] == ["FULL_CONTROL"]


async def test_bucket_acl_set_canned(server, s3):
    result = await call(
        server, "bucket_acl_set", bucket=BUCKET, canned_acl="public-read", confirm=True
    )
    assert result["ok"] is True
    assert _grant_permissions(result["grants"], ALL_USERS) == {"READ"}
    assert _grant_permissions(s3.get_bucket_acl(Bucket=BUCKET)["Grants"], ALL_USERS) == {"READ"}


async def test_bucket_acl_set_explicit_grants(server, s3):
    owner = s3.get_bucket_acl(Bucket=BUCKET)["Owner"]["ID"]
    grants = [
        {"permission": "FULL_CONTROL", "id": owner},
        {"permission": "READ", "uri": ALL_USERS},
    ]
    result = await call(server, "bucket_acl_set", bucket=BUCKET, grants=grants, confirm=True)
    assert _grant_permissions(result["grants"], ALL_USERS) == {"READ"}
    assert {g["Permission"] for g in result["grants"]} == {"FULL_CONTROL", "READ"}


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"canned_acl": "public"}, "canned_acl must be one of private, public-read"),
        ({}, "exactly one of canned_acl or grants"),
        ({"canned_acl": "private", "grants": []}, "exactly one of canned_acl or grants"),
        ({"grants": [{"permission": "ALL", "uri": ALL_USERS}]}, "grant permission must be"),
        ({"grants": [{"permission": "READ"}]}, "exactly one of id, uri or email"),
    ],
)
async def test_bucket_acl_set_rejects_bad_arguments_before_calling(server, s3, arguments, message):
    with pytest.raises(ToolError, match=message):
        await call(server, "bucket_acl_set", bucket=BUCKET, confirm=True, **arguments)
    assert len(s3.get_bucket_acl(Bucket=BUCKET)["Grants"]) == 1


async def test_bucket_acl_set_needs_confirm(server, s3):
    with pytest.raises(ToolError, match="PutBucketAcl refused: this is destructive"):
        await call(server, "bucket_acl_set", bucket=BUCKET, canned_acl="public-read")
    assert _grant_permissions(s3.get_bucket_acl(Bucket=BUCKET)["Grants"], ALL_USERS) == set()


async def test_bucket_acl_set_refused_when_read_only(read_only_server, s3):
    with pytest.raises(ToolError, match="PutBucketAcl refused: the server is read-only"):
        await call(
            read_only_server,
            "bucket_acl_set",
            bucket=BUCKET,
            canned_acl="public-read",
            confirm=True,
        )
    assert _grant_permissions(s3.get_bucket_acl(Bucket=BUCKET)["Grants"], ALL_USERS) == set()


async def test_bucket_acl_get_missing_bucket(server):
    with pytest.raises(ToolError, match="GetBucketAcl failed: NoSuchBucket: "):
        await call(server, "bucket_acl_get", bucket="no-such-bucket")


# object ACL


async def test_object_acl_get_and_set(server, s3):
    s3.put_object(Bucket=BUCKET, Key="a.txt", Body=b"a")
    before = await call(server, "object_acl_get", bucket=BUCKET, key="a.txt")
    assert before["key"] == "a.txt"
    assert [g["Permission"] for g in before["grants"]] == ["FULL_CONTROL"]
    result = await call(
        server,
        "object_acl_set",
        bucket=BUCKET,
        key="a.txt",
        canned_acl="public-read",
        confirm=True,
    )
    assert result["ok"] is True
    assert _grant_permissions(result["grants"], ALL_USERS) == {"READ"}
    grants = s3.get_object_acl(Bucket=BUCKET, Key="a.txt")["Grants"]
    assert _grant_permissions(grants, ALL_USERS) == {"READ"}


async def test_object_acl_set_explicit_grants(settings, s3):
    # moto ignores explicit object grants, so check the request the tool sends.
    s3.put_object(Bucket=BUCKET, Key="a.txt", Body=b"a")
    clients = Clients(settings)
    server = MCPServer("test")
    access.register(server, ToolContext(settings=settings, clients=clients))
    sent = []
    clients.s3().meta.events.register(
        "before-call.s3.PutObjectAcl", lambda params, **kwargs: sent.append(params)
    )
    grants = [{"permission": "READ", "uri": ALL_USERS}, {"permission": "READ", "email": "a@b.c"}]
    await call(server, "object_acl_set", bucket=BUCKET, key="a.txt", grants=grants, confirm=True)
    body = sent[0]["body"].decode()
    assert f"<URI>{ALL_USERS}</URI>" in body
    assert "<EmailAddress>a@b.c</EmailAddress>" in body
    assert "<Owner>" in body and "<ID>" in body


async def test_object_acl_set_needs_confirm(server, s3):
    s3.put_object(Bucket=BUCKET, Key="a.txt", Body=b"a")
    with pytest.raises(ToolError, match="PutObjectAcl refused: this is destructive"):
        await call(server, "object_acl_set", bucket=BUCKET, key="a.txt", canned_acl="public-read")
    grants = s3.get_object_acl(Bucket=BUCKET, Key="a.txt")["Grants"]
    assert _grant_permissions(grants, ALL_USERS) == set()


async def test_object_acl_set_refused_when_read_only(read_only_server, s3):
    s3.put_object(Bucket=BUCKET, Key="a.txt", Body=b"a")
    with pytest.raises(ToolError, match="PutObjectAcl refused: the server is read-only"):
        await call(
            read_only_server,
            "object_acl_set",
            bucket=BUCKET,
            key="a.txt",
            canned_acl="public-read",
            confirm=True,
        )
    grants = s3.get_object_acl(Bucket=BUCKET, Key="a.txt")["Grants"]
    assert _grant_permissions(grants, ALL_USERS) == set()


async def test_object_acl_get_missing_key(server, s3):
    with pytest.raises(ToolError, match="GetObjectAcl failed: NoSuchKey: "):
        await call(server, "object_acl_get", bucket=BUCKET, key="missing.txt")

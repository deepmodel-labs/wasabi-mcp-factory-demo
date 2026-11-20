import json

import pytest

from tests.helpers import ToolError, call
from tests.tools.iam_support import READ_ONLY_POLICY, assert_no_secrets, client_error, stubbed
from wasabi_mcp.clients import Clients


@pytest.fixture
def iam(settings):
    """A boto3 IAM client on moto, for setup the tools under test don't cover."""
    return Clients(settings).iam()


@pytest.fixture
def stub(rest_settings, monkeypatch):
    yield from stubbed(rest_settings, monkeypatch)


async def _group(server, name="readers"):
    return await call(server, "iam_group_create", group_name=name)


async def _member(server, group="readers", user="alice"):
    await call(server, "iam_user_create", user_name=user)
    await call(server, "iam_group_add_user", group_name=group, user_name=user, confirm=True)


# iam_group_list


async def test_group_list(server):
    await _group(server, "readers")
    await call(server, "iam_group_create", group_name="ops", path="/ops/")
    result = await call(server, "iam_group_list")
    assert {g["GroupName"] for g in result["items"]} == {"readers", "ops"}
    assert result["truncated"] is False and result["next_token"] is None
    assert_no_secrets(result)


async def test_group_list_sends_path_prefix_and_paginates(stub):
    # moto ignores PathPrefix on ListGroups, so check the request itself.
    stub.iam.add_response(
        "list_groups",
        {"Groups": [], "IsTruncated": True, "Marker": "next"},
        {"PathPrefix": "/ops/", "MaxItems": 2, "Marker": "prev"},
    )
    result = await call(
        stub.server, "iam_group_list", path_prefix="/ops/", max_items=2, continuation_token="prev"
    )
    assert result == {"items": [], "next_token": "next", "truncated": True}


# iam_group_get


async def test_group_get(server, iam):
    await _group(server)
    await _member(server)
    arn = iam.create_policy(PolicyName="ro", PolicyDocument=json.dumps(READ_ONLY_POLICY))["Policy"][
        "Arn"
    ]
    iam.attach_group_policy(GroupName="readers", PolicyArn=arn)
    iam.put_group_policy(
        GroupName="readers", PolicyName="inline", PolicyDocument=json.dumps(READ_ONLY_POLICY)
    )
    result = await call(server, "iam_group_get", group_name="readers")
    assert result["group"]["GroupName"] == "readers"
    assert result["members"] == ["alice"]
    assert result["attached_policies"] == [{"PolicyName": "ro", "PolicyArn": arn}]
    assert result["inline_policies"] == ["inline"]


async def test_group_get_missing(server):
    with pytest.raises(ToolError, match="GetGroup failed: NoSuchEntity"):
        await call(server, "iam_group_get", group_name="ghost")


# iam_group_create


async def test_group_create(server):
    result = await call(server, "iam_group_create", group_name="readers", path="/team/")
    assert result["ok"] is True
    assert result["group"]["GroupName"] == "readers"
    assert result["group"]["Arn"].endswith(":group/team/readers")


async def test_group_create_duplicate(server):
    await _group(server)
    with pytest.raises(ToolError, match="CreateGroup failed: EntityAlreadyExists"):
        await _group(server)


async def test_group_create_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="CreateGroup refused: the server is read-only"):
        await _group(read_only_server)
    assert (await call(read_only_server, "iam_group_list"))["items"] == []


# iam_group_update


async def test_group_update(server):
    await _group(server)
    result = await call(server, "iam_group_update", group_name="readers", new_group_name="viewers")
    assert result == {"ok": True, "group_name": "viewers"}
    assert (await call(server, "iam_group_get", group_name="viewers"))["group"]["GroupName"] == (
        "viewers"
    )


async def test_group_update_missing(stub):
    # moto doesn't raise for a missing group here.
    stub.iam.add_client_error(
        "update_group", **client_error("NoSuchEntity", "Group ghost not found", 404)
    )
    with pytest.raises(ToolError, match="UpdateGroup failed: NoSuchEntity"):
        await call(stub.server, "iam_group_update", group_name="ghost", new_path="/x/")


async def test_group_update_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="UpdateGroup refused: the server is read-only"):
        await call(read_only_server, "iam_group_update", group_name="a", new_group_name="b")


# iam_group_delete


async def test_group_delete(server):
    await _group(server)
    result = await call(server, "iam_group_delete", group_name="readers", confirm=True)
    assert result == {"ok": True, "group_name": "readers", "steps": ["DeleteGroup"]}
    assert (await call(server, "iam_group_list"))["items"] == []


async def test_group_delete_cascade(server, iam):
    await _group(server)
    await _member(server)
    arn = iam.create_policy(PolicyName="ro", PolicyDocument=json.dumps(READ_ONLY_POLICY))["Policy"][
        "Arn"
    ]
    iam.attach_group_policy(GroupName="readers", PolicyArn=arn)
    iam.put_group_policy(
        GroupName="readers", PolicyName="inline", PolicyDocument=json.dumps(READ_ONLY_POLICY)
    )
    result = await call(
        server, "iam_group_delete", group_name="readers", cascade=True, confirm=True
    )
    assert result["steps"] == [
        "RemoveUserFromGroup alice",
        f"DetachGroupPolicy {arn}",
        "DeleteGroupPolicy inline",
        "DeleteGroup",
    ]
    assert (await call(server, "iam_user_group_list", user_name="alice"))["items"] == []


async def test_group_delete_conflict_explains_cascade(stub):
    stub.iam.add_client_error(
        "delete_group", **client_error("DeleteConflict", "Cannot delete entity, has members")
    )
    with pytest.raises(ToolError, match="DeleteConflict.*members or policies.*cascade=true"):
        await call(stub.server, "iam_group_delete", group_name="readers", confirm=True)


async def test_group_delete_needs_confirm(server):
    await _group(server)
    with pytest.raises(ToolError, match="DeleteGroup refused: this is destructive"):
        await call(server, "iam_group_delete", group_name="readers")
    assert len((await call(server, "iam_group_list"))["items"]) == 1


async def test_group_delete_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="DeleteGroup refused: the server is read-only"):
        await call(read_only_server, "iam_group_delete", group_name="readers", confirm=True)


# iam_group_add_user


async def test_group_add_user(server):
    await _group(server)
    await call(server, "iam_user_create", user_name="alice")
    result = await call(
        server, "iam_group_add_user", group_name="readers", user_name="alice", confirm=True
    )
    assert result == {"ok": True, "group_name": "readers", "user_name": "alice"}
    assert (await call(server, "iam_group_get", group_name="readers"))["members"] == ["alice"]


async def test_group_add_user_missing_group(server):
    await call(server, "iam_user_create", user_name="alice")
    with pytest.raises(ToolError, match="AddUserToGroup failed: NoSuchEntity"):
        await call(
            server, "iam_group_add_user", group_name="ghost", user_name="alice", confirm=True
        )


async def test_group_add_user_needs_confirm(server):
    await _group(server)
    await call(server, "iam_user_create", user_name="alice")
    with pytest.raises(ToolError, match="AddUserToGroup refused: this is destructive"):
        await call(server, "iam_group_add_user", group_name="readers", user_name="alice")
    assert (await call(server, "iam_group_get", group_name="readers"))["members"] == []


async def test_group_add_user_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="AddUserToGroup refused: the server is read-only"):
        await call(
            read_only_server, "iam_group_add_user", group_name="g", user_name="u", confirm=True
        )


# iam_group_remove_user


async def test_group_remove_user(server):
    await _group(server)
    await _member(server)
    result = await call(
        server, "iam_group_remove_user", group_name="readers", user_name="alice", confirm=True
    )
    assert result == {"ok": True, "group_name": "readers", "user_name": "alice"}
    assert (await call(server, "iam_group_get", group_name="readers"))["members"] == []


async def test_group_remove_user_not_a_member(server):
    await _group(server)
    await call(server, "iam_user_create", user_name="alice")
    with pytest.raises(ToolError, match="RemoveUserFromGroup failed: NoSuchEntity"):
        await call(
            server, "iam_group_remove_user", group_name="readers", user_name="alice", confirm=True
        )


async def test_group_remove_user_needs_confirm(server):
    await _group(server)
    await _member(server)
    with pytest.raises(ToolError, match="RemoveUserFromGroup refused: this is destructive"):
        await call(server, "iam_group_remove_user", group_name="readers", user_name="alice")
    assert (await call(server, "iam_group_get", group_name="readers"))["members"] == ["alice"]


async def test_group_remove_user_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="RemoveUserFromGroup refused: the server is read-only"):
        await call(
            read_only_server, "iam_group_remove_user", group_name="g", user_name="u", confirm=True
        )


# iam_user_group_list


async def test_user_group_list(server):
    await _group(server, "readers")
    await _group(server, "ops")
    await _member(server, "readers")
    await call(server, "iam_group_add_user", group_name="ops", user_name="alice", confirm=True)
    result = await call(server, "iam_user_group_list", user_name="alice")
    assert {g["GroupName"] for g in result["items"]} == {"readers", "ops"}
    assert result["truncated"] is False


async def test_user_group_list_missing_user(server):
    with pytest.raises(ToolError, match="ListGroupsForUser failed: NoSuchEntity"):
        await call(server, "iam_user_group_list", user_name="ghost")

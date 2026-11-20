import pytest

from tests.helpers import ToolError, call
from tests.tools.iam_support import assert_no_secrets, client_error, stubbed


@pytest.fixture
def stub(rest_settings, monkeypatch):
    yield from stubbed(rest_settings, monkeypatch)


@pytest.fixture
async def alice(server):
    await call(server, "iam_user_create", user_name="alice")
    return "alice"


async def _key(server, user="alice"):
    return await call(server, "iam_access_key_create", user_name=user, confirm=True)


# iam_access_key_list


async def test_access_key_list(server, alice):
    created = await _key(server)
    result = await call(server, "iam_access_key_list", user_name=alice)
    assert result["truncated"] is False and result["next_token"] is None
    [item] = result["items"]
    assert set(item) == {"UserName", "AccessKeyId", "Status", "CreateDate"}
    assert item["AccessKeyId"] == created["access_key_id"]
    assert_no_secrets(result, created["secret_access_key"])


async def test_access_key_list_all_users_sends_wildcard(stub):
    stub.iam.add_response(
        "list_access_keys",
        {
            "AccessKeyMetadata": [
                {"AccessKeyId": "AKIAROOT0000000000000", "Status": "Active"},
                {"UserName": "alice", "AccessKeyId": "AKIAALICE000000000000", "Status": "Active"},
            ],
            "IsTruncated": True,
            "Marker": "m2",
        },
        {"UserName": "*", "MaxItems": 10},
    )
    result = await call(stub.server, "iam_access_key_list", all_users=True, max_items=10)
    assert [k["UserName"] for k in result["items"]] == ["", "alice"]
    assert result["next_token"] == "m2" and result["truncated"] is True
    assert_no_secrets(result)


async def test_access_key_list_refuses_user_and_all_users(server):
    with pytest.raises(ToolError, match="either user_name or all_users"):
        await call(server, "iam_access_key_list", user_name="alice", all_users=True)


async def test_access_key_list_missing_user(server):
    with pytest.raises(ToolError, match="ListAccessKeys failed: NoSuchEntity"):
        await call(server, "iam_access_key_list", user_name="ghost")


# iam_access_key_create


async def test_access_key_create_returns_secret_once(server, alice):
    result = await _key(server)
    assert result["ok"] is True
    assert result["user_name"] == alice
    assert result["access_key_id"].startswith("AKIA")
    assert result["secret_access_key"]
    assert result["status"] == "Active"
    assert "only once" in result["note"]
    listed = await call(server, "iam_access_key_list", user_name=alice)
    summary = await call(server, "iam_user_get", user_name=alice)
    assert_no_secrets([listed, summary], result["secret_access_key"])


async def test_access_key_create_limit(server, alice):
    await _key(server)
    await _key(server)
    with pytest.raises(ToolError, match="CreateAccessKey failed: LimitExceeded"):
        await _key(server)


async def test_access_key_create_needs_confirm(server, alice):
    with pytest.raises(ToolError, match="CreateAccessKey refused: this is destructive"):
        await call(server, "iam_access_key_create", user_name=alice)
    assert (await call(server, "iam_access_key_list", user_name=alice))["items"] == []


async def test_access_key_create_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="CreateAccessKey refused: the server is read-only"):
        await call(read_only_server, "iam_access_key_create", user_name="alice", confirm=True)


async def test_access_key_create_refusal_never_calls_wasabi(stub):
    # No stubbed response is queued: any call would fail the Stubber.
    with pytest.raises(ToolError, match="refused: this is destructive"):
        await call(stub.server, "iam_access_key_create", user_name="alice", confirm=False)


# iam_access_key_update


async def test_access_key_update(server, alice):
    key = await _key(server)
    result = await call(
        server,
        "iam_access_key_update",
        user_name=alice,
        access_key_id=key["access_key_id"],
        active=False,
        confirm=True,
    )
    assert result == {"ok": True, "access_key_id": key["access_key_id"], "status": "Inactive"}
    [item] = (await call(server, "iam_access_key_list", user_name=alice))["items"]
    assert item["Status"] == "Inactive"
    await call(
        server,
        "iam_access_key_update",
        user_name=alice,
        access_key_id=key["access_key_id"],
        active=True,
        confirm=True,
    )
    [item] = (await call(server, "iam_access_key_list", user_name=alice))["items"]
    assert item["Status"] == "Active"


async def test_access_key_update_missing_key(stub):
    stub.iam.add_client_error(
        "update_access_key", **client_error("NoSuchEntity", "key not found", 404)
    )
    with pytest.raises(ToolError, match="UpdateAccessKey failed: NoSuchEntity"):
        await call(
            stub.server,
            "iam_access_key_update",
            access_key_id="AKIAMISSING000000000",
            active=False,
            confirm=True,
        )


async def test_access_key_update_needs_confirm(server):
    with pytest.raises(ToolError, match="UpdateAccessKey refused: this is destructive"):
        await call(server, "iam_access_key_update", access_key_id="AKIAX", active=False)


async def test_access_key_update_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="UpdateAccessKey refused: the server is read-only"):
        await call(
            read_only_server,
            "iam_access_key_update",
            access_key_id="AKIAX",
            active=False,
            confirm=True,
        )


# iam_access_key_delete


async def test_access_key_delete(server, alice):
    key = await _key(server)
    result = await call(
        server,
        "iam_access_key_delete",
        user_name=alice,
        access_key_id=key["access_key_id"],
        confirm=True,
    )
    assert result == {"ok": True, "access_key_id": key["access_key_id"]}
    assert (await call(server, "iam_access_key_list", user_name=alice))["items"] == []


async def test_access_key_delete_missing_key(server, alice):
    with pytest.raises(ToolError, match="DeleteAccessKey failed: NoSuchEntity"):
        await call(
            server,
            "iam_access_key_delete",
            user_name=alice,
            access_key_id="AKIAMISSING000000000",
            confirm=True,
        )


async def test_access_key_delete_needs_confirm(server, alice):
    key = await _key(server)
    with pytest.raises(ToolError, match="DeleteAccessKey refused: this is destructive"):
        await call(
            server, "iam_access_key_delete", user_name=alice, access_key_id=key["access_key_id"]
        )
    assert len((await call(server, "iam_access_key_list", user_name=alice))["items"]) == 1


async def test_access_key_delete_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="DeleteAccessKey refused: the server is read-only"):
        await call(read_only_server, "iam_access_key_delete", access_key_id="AKIAX", confirm=True)


# iam_access_key_last_used


async def test_access_key_last_used(server, alice):
    key = await _key(server)
    result = await call(server, "iam_access_key_last_used", access_key_id=key["access_key_id"])
    assert result["access_key_id"] == key["access_key_id"]
    assert result["user_name"] == alice
    assert "ServiceName" in result["last_used"]
    assert_no_secrets(result, key["secret_access_key"])


async def test_access_key_last_used_missing_key(stub):
    stub.iam.add_client_error(
        "get_access_key_last_used", **client_error("NoSuchEntity", "key not found", 404)
    )
    with pytest.raises(ToolError, match="GetAccessKeyLastUsed failed: NoSuchEntity"):
        await call(stub.server, "iam_access_key_last_used", access_key_id="AKIAMISSING000000000")

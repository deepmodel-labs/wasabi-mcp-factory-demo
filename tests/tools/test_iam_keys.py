import pytest

from tests.helpers import ToolError, call
from tests.tools.iam_support import assert_no_secrets, client_error, stubbed
from tests.tools.secrets_support import (
    UNUSABLE_DIRS,
    assert_absent,
    call_with_text,
    read_secrets_file,
    secrets_server,
    unusable_dir,
    with_secrets_dir,
)
from wasabi_mcp.tools import _secrets


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


# iam_access_key_create with WASABI_MCP_SECRETS_DIR


@pytest.fixture
def secrets_dir(tmp_path):
    return tmp_path / "secrets"


async def test_access_key_create_without_secrets_dir_returns_the_secret(server, alice):
    result = await _key(server)
    assert set(result) == {
        "ok",
        "user_name",
        "access_key_id",
        "secret_access_key",
        "status",
        "create_date",
        "note",
    }


async def test_access_key_create_writes_the_secret_to_a_file(settings, alice, secrets_dir):
    server = secrets_server(settings, secrets_dir)
    result, text = await call_with_text(
        server, "iam_access_key_create", user_name=alice, confirm=True
    )
    assert set(result) == {
        "ok",
        "user_name",
        "access_key_id",
        "status",
        "create_date",
        "secrets_file",
        "note",
    }
    assert result["user_name"] == alice
    assert result["status"] == "Active"
    saved = read_secrets_file(result["secrets_file"], secrets_dir)
    assert saved["access_key_id"] == result["access_key_id"]
    assert_absent(text, saved["secret_access_key"])
    assert "secret_access_key" not in text


async def test_access_key_create_twice_writes_two_files(settings, alice, secrets_dir):
    server = secrets_server(settings, secrets_dir)
    first = await call(server, "iam_access_key_create", user_name=alice, confirm=True)
    second = await call(server, "iam_access_key_create", user_name=alice, confirm=True)
    assert first["secrets_file"] != second["secrets_file"]
    one = read_secrets_file(first["secrets_file"], secrets_dir)
    two = read_secrets_file(second["secrets_file"], secrets_dir)
    assert one["secret_access_key"] != two["secret_access_key"]


@pytest.mark.parametrize(("kind", "match"), UNUSABLE_DIRS)
async def test_access_key_create_refuses_an_unusable_dir_before_calling_wasabi(
    rest_settings, monkeypatch, tmp_path, kind, match
):
    # The Stubber has no queued responses: reaching IAM would fail differently.
    settings = with_secrets_dir(rest_settings, unusable_dir(tmp_path, kind))
    for stub in stubbed(settings, monkeypatch):
        with pytest.raises(ToolError, match=match):
            await _key(stub.server)


async def test_access_key_create_names_the_key_when_the_file_cant_be_written(
    rest_settings, monkeypatch, secrets_dir
):
    secrets_dir.mkdir(mode=0o700)
    taken = secrets_dir / "iam_access_key_create-taken.json"
    taken.write_text("existing")
    monkeypatch.setattr(_secrets.secrets, "token_hex", lambda _n: "taken")
    for stub in stubbed(with_secrets_dir(rest_settings, secrets_dir), monkeypatch):
        stub.iam.add_response(
            "create_access_key",
            {
                "AccessKey": {
                    "UserName": "alice",
                    "AccessKeyId": "AKIAALICE000000000000",
                    "SecretAccessKey": "brand-new-secret",
                    "Status": "Active",
                }
            },
            {"UserName": "alice"},
        )
        with pytest.raises(ToolError, match="Access key AKIAALICE000000000000 was created") as err:
            await _key(stub.server)
        assert "delete or rotate" in str(err.value)
        assert "brand-new-secret" not in str(err.value)
    assert taken.read_text() == "existing"

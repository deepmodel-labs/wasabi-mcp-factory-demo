import json

import pytest

from tests.helpers import ToolError, call
from tests.tools.iam_support import READ_ONLY_POLICY, assert_no_secrets, client_error, stubbed
from tests.tools.secrets_support import (
    UNUSABLE_DIRS,
    assert_absent,
    call_with_text,
    read_secrets_file,
    secrets_server,
    unusable_dir,
    with_secrets_dir,
)
from wasabi_mcp.clients import Clients
from wasabi_mcp.tools.iam_users import generate_password


@pytest.fixture
def iam(settings):
    """A boto3 IAM client on moto, for setup the tools under test don't cover."""
    return Clients(settings).iam()


@pytest.fixture
def stub(rest_settings, monkeypatch):
    yield from stubbed(rest_settings, monkeypatch)


async def _user(server, name="alice"):
    return await call(server, "iam_user_create", user_name=name)


# iam_user_list


async def test_user_list(server):
    await _user(server, "alice")
    await call(server, "iam_user_create", user_name="bob", path="/contractors/")
    result = await call(server, "iam_user_list")
    assert {u["UserName"] for u in result["items"]} == {"alice", "bob"}
    assert result["truncated"] is False and result["next_token"] is None
    scoped = await call(server, "iam_user_list", path_prefix="/contractors/")
    assert [u["UserName"] for u in scoped["items"]] == ["bob"]
    assert_no_secrets(result)


async def test_user_list_paginates(stub):
    stub.iam.add_response(
        "list_users",
        {"Users": [], "IsTruncated": True, "Marker": "next"},
        {"MaxItems": 1000, "Marker": "prev"},
    )
    result = await call(stub.server, "iam_user_list", max_items=5000, continuation_token="prev")
    assert result == {"items": [], "next_token": "next", "truncated": True}


# iam_user_get


async def test_user_get_summarises_user(server, iam):
    await _user(server)
    await call(server, "iam_group_create", group_name="readers")
    await call(server, "iam_group_add_user", group_name="readers", user_name="alice", confirm=True)
    key = await call(server, "iam_access_key_create", user_name="alice", confirm=True)
    arn = iam.create_policy(PolicyName="ro", PolicyDocument=json.dumps(READ_ONLY_POLICY))["Policy"][
        "Arn"
    ]
    iam.attach_user_policy(UserName="alice", PolicyArn=arn)
    iam.put_user_policy(
        UserName="alice", PolicyName="inline", PolicyDocument=json.dumps(READ_ONLY_POLICY)
    )
    result = await call(server, "iam_user_get", user_name="alice")
    assert result["user"]["UserName"] == "alice"
    assert result["groups"] == ["readers"]
    assert result["attached_policies"] == [{"PolicyName": "ro", "PolicyArn": arn}]
    assert result["inline_policies"] == ["inline"]
    assert result["access_keys"] == [
        {
            "AccessKeyId": key["access_key_id"],
            "Status": "Active",
            "CreateDate": result["access_keys"][0]["CreateDate"],
        }
    ]
    assert_no_secrets(result, key["secret_access_key"])


async def test_user_get_missing_user(server):
    with pytest.raises(ToolError, match="GetUser failed: NoSuchEntity"):
        await call(server, "iam_user_get", user_name="ghost")


# iam_user_create


async def test_user_create(server):
    result = await call(server, "iam_user_create", user_name="alice", path="/team/")
    assert result["ok"] is True
    assert result["user"]["UserName"] == "alice"
    assert result["user"]["Path"] == "/team/"
    assert result["user"]["Arn"].endswith(":user/team/alice")


async def test_user_create_duplicate(server):
    await _user(server)
    with pytest.raises(ToolError, match="CreateUser failed: EntityAlreadyExists"):
        await _user(server)


async def test_user_create_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="CreateUser refused: the server is read-only"):
        await _user(read_only_server)
    assert (await call(read_only_server, "iam_user_list"))["items"] == []


# iam_user_update


async def test_user_update(server):
    await _user(server)
    result = await call(server, "iam_user_update", user_name="alice", new_user_name="alicia")
    assert result == {"ok": True, "user_name": "alicia"}
    assert (await call(server, "iam_user_get", user_name="alicia"))["user"]["UserName"] == "alicia"


async def test_user_update_missing_user(server):
    with pytest.raises(ToolError, match="UpdateUser failed: NoSuchEntity"):
        await call(server, "iam_user_update", user_name="ghost", new_path="/x/")


async def test_user_update_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="UpdateUser refused"):
        await call(read_only_server, "iam_user_update", user_name="a", new_user_name="b")


# iam_user_delete


async def test_user_delete(server):
    await _user(server)
    result = await call(server, "iam_user_delete", user_name="alice", confirm=True)
    assert result == {"ok": True, "user_name": "alice", "steps": ["DeleteUser"]}
    assert (await call(server, "iam_user_list"))["items"] == []


async def test_user_delete_cascade_removes_dependents(server):
    await _user(server)
    await call(server, "iam_group_create", group_name="readers")
    await call(server, "iam_group_add_user", group_name="readers", user_name="alice", confirm=True)
    key = await call(server, "iam_access_key_create", user_name="alice", confirm=True)
    await call(server, "iam_login_profile_create", user_name="alice", confirm=True)
    result = await call(server, "iam_user_delete", user_name="alice", cascade=True, confirm=True)
    assert result["steps"] == [
        f"DeleteAccessKey {key['access_key_id']}",
        "DeleteLoginProfile",
        "RemoveUserFromGroup readers",
        "DeleteUser",
    ]
    assert (await call(server, "iam_group_get", group_name="readers"))["members"] == []


async def test_user_delete_cascade_detaches_policies_and_mfa(server, iam):
    await _user(server)
    arn = iam.create_policy(PolicyName="ro", PolicyDocument=json.dumps(READ_ONLY_POLICY))["Policy"][
        "Arn"
    ]
    iam.attach_user_policy(UserName="alice", PolicyArn=arn)
    iam.put_user_policy(
        UserName="alice", PolicyName="inline", PolicyDocument=json.dumps(READ_ONLY_POLICY)
    )
    serial = iam.create_virtual_mfa_device(VirtualMFADeviceName="alice-mfa")["VirtualMFADevice"][
        "SerialNumber"
    ]
    iam.enable_mfa_device(
        UserName="alice",
        SerialNumber=serial,
        AuthenticationCode1="123456",
        AuthenticationCode2="654321",
    )
    result = await call(server, "iam_user_delete", user_name="alice", cascade=True, confirm=True)
    assert result["steps"] == [
        f"DetachUserPolicy {arn}",
        "DeleteUserPolicy inline",
        f"DeactivateMFADevice {serial}",
        f"DeleteVirtualMFADevice {serial}",
        "DeleteUser",
    ]


async def test_user_delete_conflict_explains_cascade(stub):
    stub.iam.add_client_error(
        "delete_user", **client_error("DeleteConflict", "Cannot delete entity, must remove keys")
    )
    with pytest.raises(ToolError, match="DeleteConflict.*cascade=true"):
        await call(stub.server, "iam_user_delete", user_name="alice", confirm=True)


async def test_user_delete_needs_confirm(server):
    await _user(server)
    with pytest.raises(ToolError, match="DeleteUser refused: this is destructive"):
        await call(server, "iam_user_delete", user_name="alice")
    assert len((await call(server, "iam_user_list"))["items"]) == 1


async def test_user_delete_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="DeleteUser refused: the server is read-only"):
        await call(read_only_server, "iam_user_delete", user_name="alice", confirm=True)


# login profiles


async def test_login_profile_get_absent(server):
    await _user(server)
    assert await call(server, "iam_login_profile_get", user_name="alice") == {"configured": False}


async def test_login_profile_get_missing_user_is_an_error(server):
    with pytest.raises(ToolError, match="GetLoginProfile failed: NoSuchEntity"):
        await call(server, "iam_login_profile_get", user_name="ghost")


async def test_login_profile_get_present(server):
    await _user(server)
    await call(server, "iam_login_profile_create", user_name="alice", confirm=True)
    result = await call(server, "iam_login_profile_get", user_name="alice")
    assert result["configured"] is True and result["UserName"] == "alice"
    assert "password" not in json.dumps(result).lower().replace("passwordresetrequired", "")


async def test_login_profile_create_generates_password_once(server):
    await _user(server)
    result = await call(server, "iam_login_profile_create", user_name="alice", confirm=True)
    password = result["password"]
    assert len(password) == 24
    assert result["password_reset_required"] is True
    assert "only once" in result["note"]
    get = await call(server, "iam_login_profile_get", user_name="alice")
    summary = await call(server, "iam_user_get", user_name="alice")
    assert_no_secrets([get, summary], password)


async def test_login_profile_create_uses_given_password(server):
    await _user(server)
    result = await call(
        server,
        "iam_login_profile_create",
        user_name="alice",
        password="Given-Passw0rd!",
        password_reset_required=False,
        confirm=True,
    )
    assert result["password"] == "Given-Passw0rd!"
    assert result["password_reset_required"] is False


async def test_login_profile_create_missing_user(server):
    with pytest.raises(ToolError, match="CreateLoginProfile failed: NoSuchEntity"):
        await call(server, "iam_login_profile_create", user_name="ghost", confirm=True)


async def test_login_profile_create_needs_confirm(server):
    await _user(server)
    with pytest.raises(ToolError, match="CreateLoginProfile refused: this is destructive"):
        await call(server, "iam_login_profile_create", user_name="alice")
    assert await call(server, "iam_login_profile_get", user_name="alice") == {"configured": False}


async def test_login_profile_create_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="CreateLoginProfile refused: the server is read-only"):
        await call(read_only_server, "iam_login_profile_create", user_name="a", confirm=True)


async def test_login_profile_update(server):
    await _user(server)
    await call(server, "iam_login_profile_create", user_name="alice", confirm=True)
    result = await call(
        server,
        "iam_login_profile_update",
        user_name="alice",
        password="N3w-Passw0rd!",
        password_reset_required=False,
        confirm=True,
    )
    assert result == {"ok": True, "user_name": "alice"}


async def test_login_profile_update_without_profile(server):
    await _user(server)
    with pytest.raises(ToolError, match="UpdateLoginProfile failed: NoSuchEntity"):
        await call(
            server, "iam_login_profile_update", user_name="alice", password="x", confirm=True
        )


async def test_login_profile_update_needs_confirm(server):
    with pytest.raises(ToolError, match="UpdateLoginProfile refused: this is destructive"):
        await call(server, "iam_login_profile_update", user_name="alice", password="x")


async def test_login_profile_update_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="UpdateLoginProfile refused: the server is read-only"):
        await call(read_only_server, "iam_login_profile_update", user_name="a", confirm=True)


async def test_login_profile_delete(server):
    await _user(server)
    await call(server, "iam_login_profile_create", user_name="alice", confirm=True)
    result = await call(server, "iam_login_profile_delete", user_name="alice", confirm=True)
    assert result == {"ok": True, "user_name": "alice"}
    assert await call(server, "iam_login_profile_get", user_name="alice") == {"configured": False}


async def test_login_profile_delete_without_profile(server):
    await _user(server)
    with pytest.raises(ToolError, match="DeleteLoginProfile failed: NoSuchEntity"):
        await call(server, "iam_login_profile_delete", user_name="alice", confirm=True)


async def test_login_profile_delete_needs_confirm(server):
    with pytest.raises(ToolError, match="DeleteLoginProfile refused: this is destructive"):
        await call(server, "iam_login_profile_delete", user_name="alice")


async def test_login_profile_delete_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="DeleteLoginProfile refused: the server is read-only"):
        await call(read_only_server, "iam_login_profile_delete", user_name="a", confirm=True)


def test_generate_password_has_every_character_class():
    for _ in range(20):
        password = generate_password()
        assert len(password) == 24
        assert any(c.isupper() for c in password)
        assert any(c.islower() for c in password)
        assert any(c.isdigit() for c in password)
        assert any(not c.isalnum() for c in password)
    assert generate_password() != generate_password()


# iam_login_profile_create with WASABI_MCP_SECRETS_DIR


async def test_login_profile_create_without_secrets_dir_returns_the_password(server):
    await _user(server)
    result = await call(server, "iam_login_profile_create", user_name="alice", confirm=True)
    assert set(result) == {
        "ok",
        "user_name",
        "password",
        "password_reset_required",
        "create_date",
        "note",
    }


async def test_login_profile_create_writes_the_password_to_a_file(settings, tmp_path):
    directory = tmp_path / "secrets"
    server = secrets_server(settings, directory)
    await _user(server)
    result, text = await call_with_text(
        server, "iam_login_profile_create", user_name="alice", confirm=True
    )
    assert set(result) == {
        "ok",
        "user_name",
        "password_reset_required",
        "create_date",
        "secrets_file",
        "note",
    }
    assert result["user_name"] == "alice"
    assert result["password_reset_required"] is True
    saved = read_secrets_file(result["secrets_file"], directory)
    assert saved["user_name"] == "alice"
    assert len(saved["password"]) == 24
    assert_absent(text, saved["password"])


async def test_login_profile_create_keeps_a_given_password_out_of_the_result(settings, tmp_path):
    directory = tmp_path / "secrets"
    server = secrets_server(settings, directory)
    await _user(server)
    result, text = await call_with_text(
        server,
        "iam_login_profile_create",
        user_name="alice",
        password="Given-Passw0rd!",
        confirm=True,
    )
    assert read_secrets_file(result["secrets_file"], directory)["password"] == "Given-Passw0rd!"
    assert_absent(text, "Given-Passw0rd!")


@pytest.mark.parametrize(("kind", "match"), UNUSABLE_DIRS)
async def test_login_profile_create_refuses_an_unusable_dir_before_calling_wasabi(
    rest_settings, monkeypatch, tmp_path, kind, match
):
    # The Stubber has no queued responses: reaching IAM would fail differently.
    settings = with_secrets_dir(rest_settings, unusable_dir(tmp_path, kind))
    for stub in stubbed(settings, monkeypatch):
        with pytest.raises(ToolError, match=match):
            await call(stub.server, "iam_login_profile_create", user_name="alice", confirm=True)

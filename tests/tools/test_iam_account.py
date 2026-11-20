import base64
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from tests.conftest import FAKE
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

GET_USER_MFA = (
    b'<GetUserMFAResponse xmlns="https://iam.amazonaws.com/doc/2010-05-08/">'
    b"<GetUserMFAResult>"
    b"<UserName>alice</UserName>"
    b"<MFADevices><member><SerialNumber>arn:aws:iam::100000000001:mfa/alice</SerialNumber>"
    b"<EnableDate>2026-01-02T03:04:05Z</EnableDate></member></MFADevices>"
    b"<AuthenticationDate>2026-11-20T00:00:00Z</AuthenticationDate>"
    b"<CredentialsAge>3600</CredentialsAge>"
    b"</GetUserMFAResult>"
    b"<ResponseMetadata><RequestId>r1</RequestId></ResponseMetadata></GetUserMFAResponse>"
)
IAM_ERROR = (
    b"<ErrorResponse><Error><Code>NoSuchEntity</Code>"
    b"<Message>The user with name ghost cannot be found.</Message></Error></ErrorResponse>"
)
SERIAL = "arn:aws:iam::123456789012:mfa/alice-mfa"


@pytest.fixture
def stub(rest_settings, monkeypatch):
    yield from stubbed(rest_settings, monkeypatch)


async def _virtual_device(server, name="alice-mfa"):
    return await call(server, "iam_mfa_virtual_device_create", device_name=name, confirm=True)


async def _enabled_device(server, settings):
    from wasabi_mcp.clients import Clients

    await call(server, "iam_user_create", user_name="alice")
    device = await _virtual_device(server)
    Clients(settings).iam().enable_mfa_device(
        UserName="alice",
        SerialNumber=device["serial_number"],
        AuthenticationCode1="111111",
        AuthenticationCode2="222222",
    )
    return device


# iam_account_summary


async def test_account_summary(server):
    await call(server, "iam_user_create", user_name="alice")
    result = await call(server, "iam_account_summary")
    assert result["summary"]["Users"] == 1
    assert "UsersQuota" in result["summary"]
    assert_no_secrets(result)


async def test_account_summary_error(stub):
    stub.iam.add_client_error(
        "get_account_summary", **client_error("AccessDenied", "not allowed", 403)
    )
    with pytest.raises(ToolError, match="GetAccountSummary failed: AccessDenied"):
        await call(stub.server, "iam_account_summary")


# iam_account_authorization_details


async def test_account_authorization_details(server):
    await call(server, "iam_user_create", user_name="alice")
    await call(server, "iam_group_create", group_name="readers")
    result = await call(server, "iam_account_authorization_details", filter=["User", "Group"])
    assert {(i["type"], i.get("UserName") or i.get("GroupName")) for i in result["items"]} == {
        ("user", "alice"),
        ("group", "readers"),
    }
    assert result["truncated"] is False
    assert_no_secrets(result)


async def test_account_authorization_details_paginates(stub):
    stub.iam.add_response(
        "get_account_authorization_details",
        {
            "RoleDetailList": [{"RoleName": "reader"}],
            "Policies": [{"PolicyName": "ro"}],
            "IsTruncated": True,
            "Marker": "n",
        },
        {"Filter": ["Role", "LocalManagedPolicy"], "MaxItems": 2, "Marker": "p"},
    )
    result = await call(
        stub.server,
        "iam_account_authorization_details",
        filter=["Role", "LocalManagedPolicy"],
        max_items=2,
        continuation_token="p",
    )
    assert result == {
        "items": [{"type": "role", "RoleName": "reader"}, {"type": "policy", "PolicyName": "ro"}],
        "next_token": "n",
        "truncated": True,
    }


async def test_account_authorization_details_rejects_unknown_filter(server):
    with pytest.raises(ToolError):
        await call(server, "iam_account_authorization_details", filter=["Bucket"])


# password policy


async def test_password_policy_get_absent(server):
    assert await call(server, "iam_password_policy_get") == {"configured": False}


async def test_password_policy_get_error(stub):
    stub.iam.add_client_error(
        "get_account_password_policy", **client_error("AccessDenied", "no", 403)
    )
    with pytest.raises(ToolError, match="GetAccountPasswordPolicy failed: AccessDenied"):
        await call(stub.server, "iam_password_policy_get")


async def test_password_policy_set_and_get(server):
    result = await call(
        server,
        "iam_password_policy_set",
        minimum_password_length=14,
        require_symbols=True,
        password_reuse_prevention=5,
        confirm=True,
    )
    assert result["ok"] is True
    assert result["password_policy"]["MinimumPasswordLength"] == 14
    got = await call(server, "iam_password_policy_get")
    assert got["configured"] is True and got["RequireSymbols"] is True


async def test_password_policy_set_invalid(stub):
    stub.iam.add_client_error(
        "update_account_password_policy",
        **client_error("ValidationError", "MinimumPasswordLength too large", 400),
    )
    with pytest.raises(ToolError, match="UpdateAccountPasswordPolicy failed: ValidationError"):
        await call(stub.server, "iam_password_policy_set", minimum_password_length=99, confirm=True)


async def test_password_policy_set_needs_confirm(server):
    with pytest.raises(ToolError, match="UpdateAccountPasswordPolicy refused: this is destructive"):
        await call(server, "iam_password_policy_set", minimum_password_length=14)
    assert await call(server, "iam_password_policy_get") == {"configured": False}


async def test_password_policy_set_refused_read_only(read_only_server):
    with pytest.raises(
        ToolError, match="UpdateAccountPasswordPolicy refused: the server is read-only"
    ):
        await call(read_only_server, "iam_password_policy_set", confirm=True)


async def test_password_policy_delete(server):
    await call(server, "iam_password_policy_set", minimum_password_length=14, confirm=True)
    assert await call(server, "iam_password_policy_delete", confirm=True) == {"ok": True}
    assert await call(server, "iam_password_policy_get") == {"configured": False}


async def test_password_policy_delete_when_absent(server):
    with pytest.raises(ToolError, match="DeleteAccountPasswordPolicy failed: NoSuchEntity"):
        await call(server, "iam_password_policy_delete", confirm=True)


async def test_password_policy_delete_needs_confirm(server):
    with pytest.raises(ToolError, match="DeleteAccountPasswordPolicy refused: this is destructive"):
        await call(server, "iam_password_policy_delete")


async def test_password_policy_delete_refused_read_only(read_only_server):
    with pytest.raises(
        ToolError, match="DeleteAccountPasswordPolicy refused: the server is read-only"
    ):
        await call(read_only_server, "iam_password_policy_delete", confirm=True)


# account alias


async def test_account_alias_create_list_delete(server):
    created = await call(server, "iam_account_alias_create", account_alias="acme-storage")
    assert created == {"ok": True, "account_alias": "acme-storage"}
    listed = await call(server, "iam_account_alias_list")
    assert listed == {"items": ["acme-storage"], "next_token": None, "truncated": False}
    deleted = await call(
        server, "iam_account_alias_delete", account_alias="acme-storage", confirm=True
    )
    assert deleted == {"ok": True, "account_alias": "acme-storage"}
    assert (await call(server, "iam_account_alias_list"))["items"] == []


async def test_account_alias_list_paginates(stub):
    stub.iam.add_response(
        "list_account_aliases",
        {"AccountAliases": ["acme"], "IsTruncated": True, "Marker": "n"},
        {"MaxItems": 1},
    )
    result = await call(stub.server, "iam_account_alias_list", max_items=1)
    assert result == {"items": ["acme"], "next_token": "n", "truncated": True}


async def test_account_alias_create_taken(stub):
    stub.iam.add_client_error(
        "create_account_alias", **client_error("EntityAlreadyExists", "alias taken")
    )
    with pytest.raises(ToolError, match="CreateAccountAlias failed: EntityAlreadyExists"):
        await call(stub.server, "iam_account_alias_create", account_alias="taken")


async def test_account_alias_create_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="CreateAccountAlias refused: the server is read-only"):
        await call(read_only_server, "iam_account_alias_create", account_alias="acme")
    assert (await call(read_only_server, "iam_account_alias_list"))["items"] == []


async def test_account_alias_delete_missing(stub):
    stub.iam.add_client_error(
        "delete_account_alias", **client_error("NoSuchEntity", "no alias", 404)
    )
    with pytest.raises(ToolError, match="DeleteAccountAlias failed: NoSuchEntity"):
        await call(stub.server, "iam_account_alias_delete", account_alias="acme", confirm=True)


async def test_account_alias_delete_needs_confirm(server):
    await call(server, "iam_account_alias_create", account_alias="acme-storage")
    with pytest.raises(ToolError, match="DeleteAccountAlias refused: this is destructive"):
        await call(server, "iam_account_alias_delete", account_alias="acme-storage")
    assert (await call(server, "iam_account_alias_list"))["items"] == ["acme-storage"]


async def test_account_alias_delete_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="DeleteAccountAlias refused: the server is read-only"):
        await call(read_only_server, "iam_account_alias_delete", account_alias="a", confirm=True)


# iam_mfa_device_list


async def test_mfa_device_list_for_user(server, settings):
    device = await _enabled_device(server, settings)
    result = await call(server, "iam_mfa_device_list", user_name="alice")
    [item] = result["items"]
    assert item["SerialNumber"] == device["serial_number"] and item["UserName"] == "alice"
    assert_no_secrets(result, device["base32_seed"], device["qr_png_base64"])


async def test_mfa_device_list_virtual_never_returns_seeds(server, settings):
    device = await _enabled_device(server, settings)
    unassigned = await _virtual_device(server, "spare")
    result = await call(server, "iam_mfa_device_list", assignment_status="Any")
    assert {d["SerialNumber"] for d in result["items"]} == {
        device["serial_number"],
        unassigned["serial_number"],
    }
    for item in result["items"]:
        assert not {"Base32StringSeed", "QRCodePNG"} & set(item)
    assert_no_secrets(
        result,
        device["base32_seed"],
        device["qr_png_base64"],
        unassigned["base32_seed"],
        unassigned["qr_png_base64"],
    )
    assigned = await call(server, "iam_mfa_device_list", assignment_status="Assigned")
    assert [d["SerialNumber"] for d in assigned["items"]] == [device["serial_number"]]


async def test_mfa_device_list_missing_user(server):
    with pytest.raises(ToolError, match="ListMFADevices failed: NoSuchEntity"):
        await call(server, "iam_mfa_device_list", user_name="ghost")


# iam_user_mfa_get (Wasabi extension via rest.iam_query)


@respx.mock
async def test_user_mfa_get_sends_signed_form_post(rest_server):
    route = respx.post(f"{FAKE['iam']}/").mock(
        return_value=httpx.Response(200, content=GET_USER_MFA)
    )
    result = await call(rest_server, "iam_user_mfa_get", user_name="alice")
    assert result == {
        "mfa": {
            "UserName": "alice",
            "MFADevices": [
                {
                    "SerialNumber": "arn:aws:iam::100000000001:mfa/alice",
                    "EnableDate": "2026-01-02T03:04:05Z",
                }
            ],
            "AuthenticationDate": "2026-11-20T00:00:00Z",
            "CredentialsAge": "3600",
        }
    }
    request = route.calls.last.request
    assert request.method == "POST"
    assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 ")
    assert "/us-east-1/iam/aws4_request" in request.headers["authorization"]
    assert parse_qs(request.content.decode()) == {
        "Action": ["GetUserMFA"],
        "Version": ["2010-05-08"],
        "UserName": ["alice"],
    }
    assert_no_secrets(result)


@respx.mock
async def test_user_mfa_get_for_caller_omits_user_name(rest_server):
    route = respx.post(f"{FAKE['iam']}/").mock(
        return_value=httpx.Response(200, content=GET_USER_MFA)
    )
    await call(rest_server, "iam_user_mfa_get")
    form = parse_qs(route.calls.last.request.content.decode())
    assert form == {"Action": ["GetUserMFA"], "Version": ["2010-05-08"]}


@respx.mock
async def test_user_mfa_get_strips_seed_fields(rest_server):
    respx.post(f"{FAKE['iam']}/").mock(
        return_value=httpx.Response(
            200,
            content=b"<GetUserMFAResponse><GetUserMFAResult><MFADevices><member>"
            b"<SerialNumber>s</SerialNumber><Base32StringSeed>SEEDSEED</Base32StringSeed>"
            b"</member></MFADevices></GetUserMFAResult></GetUserMFAResponse>",
        )
    )
    result = await call(rest_server, "iam_user_mfa_get", user_name="alice")
    assert result == {"mfa": {"MFADevices": [{"SerialNumber": "s"}]}}


@respx.mock
async def test_user_mfa_get_reports_wasabi_error(rest_server):
    respx.post(f"{FAKE['iam']}/").mock(return_value=httpx.Response(404, content=IAM_ERROR))
    with pytest.raises(ToolError, match="GetUserMFA failed: NoSuchEntity"):
        await call(rest_server, "iam_user_mfa_get", user_name="ghost")


# iam_mfa_virtual_device_create


async def test_mfa_virtual_device_create_returns_seed_once(server):
    result = await _virtual_device(server)
    assert result["ok"] is True
    assert result["serial_number"] == SERIAL
    assert result["base32_seed"].isalnum()
    assert base64.b64decode(result["qr_png_base64"])
    assert "only once" in result["note"]


async def test_mfa_virtual_device_create_encodes_png(stub):
    png = b"\x89PNG\r\n\x1a\nfake"
    stub.iam.add_response(
        "create_virtual_mfa_device",
        {
            "VirtualMFADevice": {
                "SerialNumber": SERIAL,
                "Base32StringSeed": b"JBSWY3DPEHPK3PXP",
                "QRCodePNG": png,
            }
        },
        {"VirtualMFADeviceName": "alice-mfa"},
    )
    result = await _virtual_device(stub.server)
    assert result["base32_seed"] == "JBSWY3DPEHPK3PXP"
    assert base64.b64decode(result["qr_png_base64"]) == png


async def test_mfa_virtual_device_create_duplicate(server):
    await _virtual_device(server)
    with pytest.raises(ToolError, match="CreateVirtualMFADevice failed: EntityAlreadyExists"):
        await _virtual_device(server)


async def test_mfa_virtual_device_create_needs_confirm(server):
    with pytest.raises(ToolError, match="CreateVirtualMFADevice refused: this is destructive"):
        await call(server, "iam_mfa_virtual_device_create", device_name="alice-mfa")
    assert (await call(server, "iam_mfa_device_list", assignment_status="Any"))["items"] == []


async def test_mfa_virtual_device_create_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="CreateVirtualMFADevice refused: the server is read-only"):
        await _virtual_device(read_only_server)


# iam_mfa_device_deactivate


async def test_mfa_device_deactivate(server, settings):
    device = await _enabled_device(server, settings)
    result = await call(
        server,
        "iam_mfa_device_deactivate",
        user_name="alice",
        serial_number=device["serial_number"],
        confirm=True,
    )
    assert result == {
        "ok": True,
        "serial_number": device["serial_number"],
        "steps": ["DeactivateMFADevice"],
    }
    assert (await call(server, "iam_mfa_device_list", user_name="alice"))["items"] == []
    spare = await call(server, "iam_mfa_device_list", assignment_status="Unassigned")
    assert [d["SerialNumber"] for d in spare["items"]] == [device["serial_number"]]


async def test_mfa_device_deactivate_and_delete(server, settings):
    device = await _enabled_device(server, settings)
    result = await call(
        server,
        "iam_mfa_device_deactivate",
        user_name="alice",
        serial_number=device["serial_number"],
        delete=True,
        confirm=True,
    )
    assert result["steps"] == ["DeactivateMFADevice", "DeleteVirtualMFADevice"]
    assert (await call(server, "iam_mfa_device_list", assignment_status="Any"))["items"] == []


async def test_mfa_device_deactivate_missing(server):
    await call(server, "iam_user_create", user_name="alice")
    with pytest.raises(ToolError, match="DeactivateMFADevice failed: NoSuchEntity"):
        await call(
            server,
            "iam_mfa_device_deactivate",
            user_name="alice",
            serial_number=SERIAL,
            confirm=True,
        )


async def test_mfa_device_deactivate_needs_confirm(server):
    with pytest.raises(ToolError, match="DeactivateMFADevice refused: this is destructive"):
        await call(server, "iam_mfa_device_deactivate", user_name="alice", serial_number=SERIAL)


async def test_mfa_device_deactivate_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="DeactivateMFADevice refused: the server is read-only"):
        await call(
            read_only_server,
            "iam_mfa_device_deactivate",
            user_name="alice",
            serial_number=SERIAL,
            confirm=True,
        )


# iam_mfa_virtual_device_create with WASABI_MCP_SECRETS_DIR


async def test_mfa_virtual_device_create_without_secrets_dir_returns_the_seed(server):
    result = await _virtual_device(server)
    assert set(result) == {"ok", "serial_number", "base32_seed", "qr_png_base64", "note"}


async def test_mfa_virtual_device_create_writes_seed_and_png_to_one_file(settings, tmp_path):
    directory = tmp_path / "secrets"
    server = secrets_server(settings, directory)
    result, text = await call_with_text(
        server, "iam_mfa_virtual_device_create", device_name="alice-mfa", confirm=True
    )
    assert set(result) == {"ok", "serial_number", "secrets_file", "note"}
    assert result["serial_number"] == SERIAL
    saved = read_secrets_file(result["secrets_file"], directory)
    assert saved["serial_number"] == SERIAL
    assert saved["base32_string_seed"].isalnum()
    assert base64.b64decode(saved["qr_code_png"])
    assert_absent(text, saved["base32_string_seed"], saved["qr_code_png"])


async def test_mfa_virtual_device_create_saves_the_exact_png(rest_settings, monkeypatch, tmp_path):
    directory = tmp_path / "secrets"
    png = b"\x89PNG\r\n\x1a\nfake"
    for stub in stubbed(with_secrets_dir(rest_settings, directory), monkeypatch):
        stub.iam.add_response(
            "create_virtual_mfa_device",
            {
                "VirtualMFADevice": {
                    "SerialNumber": SERIAL,
                    "Base32StringSeed": b"JBSWY3DPEHPK3PXP",
                    "QRCodePNG": png,
                }
            },
            {"VirtualMFADeviceName": "alice-mfa"},
        )
        result, text = await call_with_text(
            stub.server, "iam_mfa_virtual_device_create", device_name="alice-mfa", confirm=True
        )
    saved = read_secrets_file(result["secrets_file"], directory)
    assert saved["base32_string_seed"] == "JBSWY3DPEHPK3PXP"
    assert base64.b64decode(saved["qr_code_png"]) == png
    assert_absent(text, "JBSWY3DPEHPK3PXP", base64.b64encode(png).decode())


@pytest.mark.parametrize(("kind", "match"), UNUSABLE_DIRS)
async def test_mfa_virtual_device_create_refuses_an_unusable_dir_before_calling_wasabi(
    rest_settings, monkeypatch, tmp_path, kind, match
):
    # The Stubber has no queued responses: reaching IAM would fail differently.
    settings = with_secrets_dir(rest_settings, unusable_dir(tmp_path, kind))
    for stub in stubbed(settings, monkeypatch):
        with pytest.raises(ToolError, match=match):
            await _virtual_device(stub.server)

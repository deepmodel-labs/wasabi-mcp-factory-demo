import httpx
import pytest
import respx

from tests.tools.secrets_support import (
    UNUSABLE_DIRS,
    assert_absent,
    call_with_text,
    read_secrets_file,
    secrets_server,
    unusable_dir,
)
from tests.tools.wacm_support import (
    WACM,
    assert_basic_auth,
    assert_no_secrets,
    assert_refused,
    body,
    invoke,
    read_only_rest_server,
)

NEW_ACCESS_KEY = "AKIANEWSUBACCOUNT001"
NEW_SECRET_KEY = "new-sub-account-secret"

S6_ARGS = {
    "name": "tenant-acme",
    "wasabi_account_email": "ops@acme.example",
    "send_welcome_email": True,
    "control_account_id": 3,
    "purchased_storage_tb": 10,
    "storage_quota_type": "Hard",
}

CREATED = {
    "data": {
        "id": 42,
        "name": "tenant-acme",
        "purchasedStorageTB": 10,
        "storageQuotaType": "Hard",
        "accessKey": NEW_ACCESS_KEY,
        "secretKey": NEW_SECRET_KEY,
    }
}

DESTRUCTIVE_CALLS = [
    ("wacm_sub_account_create", S6_ARGS),
    ("wacm_sub_account_update", {"sub_account_id": 42, "active": False}),
    ("wacm_sub_account_delete", {"sub_account_id": 42}),
]


@respx.mock
async def test_s6_create_with_a_10_tb_hard_quota(rest_server):
    route = respx.post(f"{WACM}/v1/sub-accounts").mock(
        return_value=httpx.Response(201, json=CREATED)
    )
    created = await invoke(rest_server, "wacm_sub_account_create", **S6_ARGS, confirm=True)
    request = route.calls.last.request
    assert str(request.url) == f"{WACM}/v1/sub-accounts"
    assert_basic_auth(request)
    assert body(request) == {
        "name": "tenant-acme",
        "wasabiAccountEmail": "ops@acme.example",
        "sendWelcomeEmail": True,
        "controlAccountId": 3,
        "purchasedStorageTB": 10,
        "storageQuotaType": "Hard",
    }
    assert "includeKeys" not in request.url.params
    assert created["access_key_id"] == NEW_ACCESS_KEY
    assert created["secret_access_key"] == NEW_SECRET_KEY
    # The secret is returned once, in its named field, not inside the account record.
    assert created["sub_account"] == {
        "data": {
            "id": 42,
            "name": "tenant-acme",
            "purchasedStorageTB": 10,
            "storageQuotaType": "Hard",
            "accessKey": NEW_ACCESS_KEY,
        }
    }
    assert_no_secrets(created)


@respx.mock
async def test_create_without_confirm_returns_no_keys(rest_server):
    route = respx.route(host="wacm.wasabi.test")
    await assert_refused(rest_server, "wacm_sub_account_create", "confirm=true", **S6_ARGS)
    assert route.call_count == 0


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"name": "ab"}, "3-100 characters"),
        ({"name": "x" * 101}, "3-100 characters"),
        ({"purchased_storage_tb": None}, "needs purchased_storage_tb"),
        ({"send_welcome_email": None}, "password or send_welcome_email"),
    ],
)
@respx.mock
async def test_create_validates_before_calling_wacm(rest_server, changes, match):
    route = respx.route(host="wacm.wasabi.test")
    arguments = {**S6_ARGS, **changes, "confirm": True}
    await assert_refused(rest_server, "wacm_sub_account_create", match, **arguments)
    assert route.call_count == 0


@respx.mock
async def test_create_with_a_password_and_trial(rest_server):
    route = respx.post(f"{WACM}/v1/sub-accounts").mock(
        return_value=httpx.Response(201, json={"data": {"id": 43}})
    )
    created = await invoke(
        rest_server,
        "wacm_sub_account_create",
        name="trial-co",
        wasabi_account_email="t@example.com",
        password="S3cure!pass",
        channel_account_id=5,
        is_trial=True,
        trial_quota_tb=1,
        trial_days=30,
        storage_quota_type="Soft",
        confirm=True,
    )
    assert body(route.calls.last.request) == {
        "name": "trial-co",
        "wasabiAccountEmail": "t@example.com",
        "password": "S3cure!pass",
        "channelAccountId": 5,
        "isTrial": True,
        "trialQuotaTB": 1,
        "trialDays": 30,
        "storageQuotaType": "Soft",
    }
    assert created == {
        "sub_account": {"data": {"id": 43}},
        "access_key_id": None,
        "secret_access_key": None,
    }


@respx.mock
async def test_create_reports_wacm_errors(rest_server):
    respx.post(f"{WACM}/v1/sub-accounts").mock(
        return_value=httpx.Response(409, json={"error": "Email already exists", "code": 409})
    )
    await assert_refused(
        rest_server,
        "wacm_sub_account_create",
        "HTTP 409: Email already exists",
        **S6_ARGS,
        confirm=True,
    )


@respx.mock
async def test_list_sends_filters_and_never_include_keys(rest_server):
    route = respx.get(f"{WACM}/v1/sub-accounts").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"id": 42, "secretKey": NEW_SECRET_KEY}], "page": 0, "total": 1},
        )
    )
    page = await invoke(
        rest_server,
        "wacm_sub_account_list",
        control_account_id=3,
        status="PAID_ACCOUNT",
        include_deleted=False,
    )
    params = route.calls.last.request.url.params
    assert dict(params) == {
        "controlAccountId": "3",
        "status": "PAID_ACCOUNT",
        "includeDeleted": "false",
    }
    assert page["items"] == [{"id": 42}]
    assert page["truncated"] is False


@respx.mock
async def test_get_drops_keys(rest_server):
    respx.get(f"{WACM}/v1/sub-accounts/42").mock(return_value=httpx.Response(200, json=CREATED))
    account = await invoke(rest_server, "wacm_sub_account_get", sub_account_id=42)
    assert "secretKey" not in account["data"]
    assert account["data"]["id"] == 42


@respx.mock
async def test_get_reports_not_found(rest_server):
    respx.get(f"{WACM}/v1/sub-accounts/9").mock(
        return_value=httpx.Response(404, json={"error": "Sub-account not found", "code": 404})
    )
    await assert_refused(
        rest_server, "wacm_sub_account_get", "HTTP 404: Sub-account not found", sub_account_id=9
    )


@respx.mock
async def test_update_patches_only_given_fields(rest_server):
    route = respx.patch(f"{WACM}/v1/sub-accounts/42").mock(
        return_value=httpx.Response(200, json={"data": {"id": 42, "active": False}})
    )
    updated = await invoke(
        rest_server,
        "wacm_sub_account_update",
        sub_account_id=42,
        active=False,
        trial_to_paid=True,
        purchased_storage_tb=20,
        storage_quota_type="Hard",
        reset_keys=True,
        reset_mfa=True,
        confirm=True,
    )
    assert body(route.calls.last.request) == {
        "active": False,
        "trialToPaid": True,
        "purchasedStorageTB": 20,
        "storageQuotaType": "Hard",
        "resetKeys": True,
        "resetMFA": True,
    }
    assert updated == {"data": {"id": 42, "active": False}}


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        ({}, "at least one field"),
        ({"storage_quota_type": "Hard"}, "needs purchased_storage_tb"),
    ],
)
@respx.mock
async def test_update_validates_before_calling_wacm(rest_server, arguments, match):
    route = respx.route(host="wacm.wasabi.test")
    await assert_refused(
        rest_server,
        "wacm_sub_account_update",
        match,
        sub_account_id=42,
        **arguments,
        confirm=True,
    )
    assert route.call_count == 0


@respx.mock
async def test_delete(rest_server):
    route = respx.delete(f"{WACM}/v1/sub-accounts/42").mock(return_value=httpx.Response(204))
    result = await invoke(rest_server, "wacm_sub_account_delete", sub_account_id=42, confirm=True)
    assert result == {"ok": True, "sub_account_id": 42}
    assert_basic_auth(route.calls.last.request)


@respx.mock
async def test_bucket_list(rest_server):
    route = respx.get(f"{WACM}/v1/sub-accounts/42/buckets").mock(
        return_value=httpx.Response(200, json={"data": [{"name": "media"}], "total": 1})
    )
    page = await invoke(
        rest_server,
        "wacm_sub_account_bucket_list",
        sub_account_id=42,
        latest=True,
        region="eu-central-1",
    )
    assert dict(route.calls.last.request.url.params) == {
        "latest": "true",
        "region": "eu-central-1",
    }
    assert page["items"] == [{"name": "media"}]


@pytest.mark.parametrize(("tool", "arguments"), DESTRUCTIVE_CALLS)
@respx.mock
async def test_destructive_tools_refuse_when_read_only(rest_settings, tool, arguments):
    route = respx.route(host="wacm.wasabi.test")
    server = read_only_rest_server(rest_settings)
    await assert_refused(server, tool, "read-only", **arguments, confirm=True)
    assert route.call_count == 0


@pytest.mark.parametrize(("tool", "arguments"), DESTRUCTIVE_CALLS)
@respx.mock
async def test_destructive_tools_need_confirm(rest_server, tool, arguments):
    route = respx.route(host="wacm.wasabi.test")
    await assert_refused(rest_server, tool, "confirm=true", **arguments)
    assert route.call_count == 0


# wacm_sub_account_create with WASABI_MCP_SECRETS_DIR


@respx.mock
async def test_create_writes_the_new_keys_to_a_file(rest_settings, tmp_path):
    directory = tmp_path / "secrets"
    respx.post(f"{WACM}/v1/sub-accounts").mock(return_value=httpx.Response(201, json=CREATED))
    server = secrets_server(rest_settings, directory)
    created, text = await call_with_text(server, "wacm_sub_account_create", **S6_ARGS, confirm=True)
    assert set(created) == {"sub_account", "access_key_id", "secrets_file"}
    assert created["access_key_id"] == NEW_ACCESS_KEY
    assert created["sub_account"]["data"]["id"] == 42
    saved = read_secrets_file(created["secrets_file"], directory)
    assert saved == {"access_key_id": NEW_ACCESS_KEY, "secret_access_key": NEW_SECRET_KEY}
    assert_absent(text, NEW_SECRET_KEY)
    assert_no_secrets(created)


@respx.mock
async def test_create_without_returned_keys_writes_no_file(rest_settings, tmp_path):
    directory = tmp_path / "secrets"
    respx.post(f"{WACM}/v1/sub-accounts").mock(
        return_value=httpx.Response(201, json={"data": {"id": 43}})
    )
    server = secrets_server(rest_settings, directory)
    created = await invoke(server, "wacm_sub_account_create", **S6_ARGS, confirm=True)
    assert created == {
        "sub_account": {"data": {"id": 43}},
        "access_key_id": None,
        "secrets_file": None,
    }
    assert list(directory.iterdir()) == []


@pytest.mark.parametrize(("kind", "match"), UNUSABLE_DIRS)
@respx.mock
async def test_create_refuses_an_unusable_dir_before_calling_wacm(
    rest_settings, tmp_path, kind, match
):
    route = respx.route(host="wacm.wasabi.test")
    server = secrets_server(rest_settings, unusable_dir(tmp_path, kind))
    await assert_refused(server, "wacm_sub_account_create", match, **S6_ARGS, confirm=True)
    assert route.call_count == 0

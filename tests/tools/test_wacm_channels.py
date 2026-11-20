import httpx
import pytest
import respx

from tests.tools.wacm_support import (
    WACM,
    assert_basic_auth,
    assert_no_secrets,
    assert_refused,
    body,
    invoke,
    read_only_rest_server,
)

USER_ARGS = {
    "channel_account_id": 5,
    "email": "op@example.com",
    "first_name": "Op",
    "last_name": "Erator",
    "role": "ADMIN",
}

WRITE_CALLS = [
    ("wacm_channel_account_create", {"name": "east", "contact_email": "c@example.com"}),
    ("wacm_channel_account_update", {"channel_account_id": 5, "name": "west"}),
    ("wacm_channel_user_create", USER_ARGS),
]
DESTRUCTIVE_CALLS = [
    ("wacm_channel_account_delete", {"channel_account_id": 5}),
    ("wacm_channel_user_update", {"user_id": 8, "active": False}),
    ("wacm_channel_user_delete", {"user_id": 8}),
]


@respx.mock
async def test_channel_account_list(rest_server):
    route = respx.get(f"{WACM}/v1/channel-accounts").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": 5}], "page": 1, "size": 1, "totalPages": 3}
        )
    )
    page = await invoke(rest_server, "wacm_channel_account_list", page=1, size=1)
    assert_basic_auth(route.calls.last.request)
    assert dict(route.calls.last.request.url.params) == {"page": "1", "size": "1"}
    assert page["items"] == [{"id": 5}]
    assert (page["next_token"], page["truncated"]) == ("2", True)


@respx.mock
async def test_channel_account_get(rest_server):
    respx.get(f"{WACM}/v1/channel-accounts/5").mock(
        return_value=httpx.Response(200, json={"data": {"id": 5, "name": "east"}})
    )
    account = await invoke(rest_server, "wacm_channel_account_get", channel_account_id=5)
    assert account == {"data": {"id": 5, "name": "east"}}


@respx.mock
async def test_channel_account_get_reports_not_found(rest_server):
    respx.get(f"{WACM}/v1/channel-accounts/6").mock(
        return_value=httpx.Response(404, json={"error": "Channel account not found", "code": 404})
    )
    await assert_refused(
        rest_server,
        "wacm_channel_account_get",
        "HTTP 404: Channel account not found",
        channel_account_id=6,
    )


@respx.mock
async def test_channel_account_create_posts_the_body(rest_server):
    route = respx.post(f"{WACM}/v1/channel-accounts").mock(
        return_value=httpx.Response(201, json={"data": {"id": 5}})
    )
    created = await invoke(
        rest_server,
        "wacm_channel_account_create",
        name="east",
        contact_email="c@example.com",
        purchased_storage=20,
        storage_quota_type="Hard",
    )
    request = route.calls.last.request
    assert_basic_auth(request)
    assert body(request) == {
        "name": "east",
        "contactEmail": "c@example.com",
        "purchasedStorage": 20,
        "storageQuotaType": "Hard",
    }
    assert created == {"data": {"id": 5}}


@respx.mock
async def test_channel_account_update_puts_only_given_fields(rest_server):
    route = respx.put(f"{WACM}/v1/channel-accounts/5").mock(
        return_value=httpx.Response(200, json={"data": {"id": 5, "purchasedStorage": 30}})
    )
    await invoke(
        rest_server, "wacm_channel_account_update", channel_account_id=5, purchased_storage=30
    )
    assert body(route.calls.last.request) == {"purchasedStorage": 30}


@respx.mock
async def test_channel_account_update_needs_a_field(rest_server):
    route = respx.route(host="wacm.wasabi.test")
    await assert_refused(
        rest_server, "wacm_channel_account_update", "at least one field", channel_account_id=5
    )
    assert route.call_count == 0


@respx.mock
async def test_channel_account_delete(rest_server):
    route = respx.delete(f"{WACM}/v1/channel-accounts/5").mock(return_value=httpx.Response(204))
    result = await invoke(
        rest_server, "wacm_channel_account_delete", channel_account_id=5, confirm=True
    )
    assert result == {"ok": True, "channel_account_id": 5}
    assert_basic_auth(route.calls.last.request)


async def test_channel_account_delete_says_it_unlinks_sub_accounts(rest_server):
    tools = {tool.name: tool for tool in await rest_server.list_tools()}
    assert "unlinks" in tools["wacm_channel_account_delete"].description


@respx.mock
async def test_channel_user_create_never_returns_the_password(rest_server):
    route = respx.post(f"{WACM}/v1/channel-accounts/users").mock(
        return_value=httpx.Response(201, json={"data": {"id": 8, "password": "hunter2"}})
    )
    created = await invoke(rest_server, "wacm_channel_user_create", **USER_ARGS, password="hunter2")
    assert body(route.calls.last.request) == {
        "channelAccountId": 5,
        "email": "op@example.com",
        "firstName": "Op",
        "lastName": "Erator",
        "role": "ADMIN",
        "password": "hunter2",
    }
    assert created == {"data": {"id": 8}}
    assert_no_secrets(created)


@respx.mock
async def test_channel_user_update_puts_the_changes(rest_server):
    route = respx.put(f"{WACM}/v1/channel-accounts/users/8").mock(
        return_value=httpx.Response(200, json={"data": {"id": 8, "active": False}})
    )
    updated = await invoke(
        rest_server,
        "wacm_channel_user_update",
        user_id=8,
        role="VIEWER",
        active=False,
        reset_mfa=True,
        confirm=True,
    )
    assert body(route.calls.last.request) == {"role": "VIEWER", "active": False, "resetMFA": True}
    assert updated == {"data": {"id": 8, "active": False}}


@respx.mock
async def test_channel_user_delete(rest_server):
    respx.delete(f"{WACM}/v1/channel-accounts/users/8").mock(return_value=httpx.Response(204))
    result = await invoke(rest_server, "wacm_channel_user_delete", user_id=8, confirm=True)
    assert result == {"ok": True, "user_id": 8}


@respx.mock
async def test_channel_user_delete_reports_wacm_errors(rest_server):
    respx.delete(f"{WACM}/v1/channel-accounts/users/9").mock(
        return_value=httpx.Response(404, json={"error": "User not found", "code": 404})
    )
    await assert_refused(
        rest_server, "wacm_channel_user_delete", "HTTP 404: User not found", user_id=9, confirm=True
    )


@pytest.mark.parametrize(("tool", "arguments"), WRITE_CALLS + DESTRUCTIVE_CALLS)
@respx.mock
async def test_writes_refuse_when_read_only(rest_settings, tool, arguments):
    route = respx.route(host="wacm.wasabi.test")
    server = read_only_rest_server(rest_settings)
    confirm = {"confirm": True} if (tool, arguments) in DESTRUCTIVE_CALLS else {}
    await assert_refused(server, tool, "read-only", **arguments, **confirm)
    assert route.call_count == 0


@pytest.mark.parametrize(("tool", "arguments"), DESTRUCTIVE_CALLS)
@respx.mock
async def test_destructive_tools_need_confirm(rest_server, tool, arguments):
    route = respx.route(host="wacm.wasabi.test")
    await assert_refused(rest_server, tool, "confirm=true", **arguments)
    assert route.call_count == 0

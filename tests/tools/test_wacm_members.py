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

MEMBER_ARGS = {
    "first_name": "Grace",
    "last_name": "Hopper",
    "email": "grace@example.com",
    "password": "Compil3r!",
    "member_role": "ADMIN",
}

DESTRUCTIVE_CALLS = [
    ("wacm_member_create", MEMBER_ARGS),
    ("wacm_member_update", {"member_id": 7, "active": False}),
    ("wacm_member_delete", {"member_id": 7}),
]


@respx.mock
async def test_member_list_sends_filters(rest_server):
    route = respx.get(f"{WACM}/v1/members").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": 7}], "page": 0, "size": 1, "total": 2}
        )
    )
    page = await invoke(rest_server, "wacm_member_list", size=1, status="ACTIVE", sub_account_id=42)
    assert_basic_auth(route.calls.last.request)
    assert dict(route.calls.last.request.url.params) == {
        "size": "1",
        "status": "ACTIVE",
        "subAccountId": "42",
    }
    assert page["items"] == [{"id": 7}]
    assert page["next_token"] == "1"


@respx.mock
async def test_member_get(rest_server):
    respx.get(f"{WACM}/v1/members/7").mock(
        return_value=httpx.Response(200, json={"data": {"id": 7, "username": "grace"}})
    )
    assert await invoke(rest_server, "wacm_member_get", member_id=7) == {
        "data": {"id": 7, "username": "grace"}
    }


@respx.mock
async def test_member_get_reports_not_found(rest_server):
    respx.get(f"{WACM}/v1/members/8").mock(
        return_value=httpx.Response(404, json={"error": "Member not found", "code": 404})
    )
    await assert_refused(rest_server, "wacm_member_get", "HTTP 404: Member not found", member_id=8)


@respx.mock
async def test_member_create_posts_the_body_and_hides_the_password(rest_server):
    route = respx.post(f"{WACM}/v1/members").mock(
        return_value=httpx.Response(201, json={"data": {"id": 7, "password": "Compil3r!"}})
    )
    created = await invoke(
        rest_server,
        "wacm_member_create",
        **MEMBER_ARGS,
        username="grace",
        password_reset_required=True,
        confirm=True,
    )
    assert body(route.calls.last.request) == {
        "firstName": "Grace",
        "lastName": "Hopper",
        "email": "grace@example.com",
        "password": "Compil3r!",
        "memberRole": "ADMIN",
        "username": "grace",
        "passwordResetRequired": True,
    }
    assert created == {"data": {"id": 7}}
    assert_no_secrets(created)


@respx.mock
async def test_member_update_puts_the_changes(rest_server):
    route = respx.put(f"{WACM}/v1/members/7").mock(
        return_value=httpx.Response(200, json={"data": {"id": 7}})
    )
    await invoke(
        rest_server,
        "wacm_member_update",
        member_id=7,
        member_role="READ_ONLY",
        reset_mfa=True,
        reinstate=True,
        confirm=True,
    )
    assert body(route.calls.last.request) == {
        "memberRole": "READ_ONLY",
        "resetMFA": True,
        "reinstate": True,
    }


@respx.mock
async def test_member_update_needs_a_field(rest_server):
    route = respx.route(host="wacm.wasabi.test")
    await assert_refused(
        rest_server, "wacm_member_update", "at least one field", member_id=7, confirm=True
    )
    assert route.call_count == 0


@respx.mock
async def test_member_delete(rest_server):
    respx.delete(f"{WACM}/v1/members/7").mock(return_value=httpx.Response(204))
    result = await invoke(rest_server, "wacm_member_delete", member_id=7, confirm=True)
    assert result == {"ok": True, "member_id": 7}


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

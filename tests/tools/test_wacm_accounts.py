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

ACCOUNT_ARGS = {
    "first_name": "Ada",
    "last_name": "Lovelace",
    "email": "ada@example.com",
    "country": "United Kingdom",
    "storage_amount": "1-5 TB",
}


@respx.mock
async def test_standalone_account_list_sends_filters_and_maps_the_page(rest_server):
    route = respx.get(f"{WACM}/v1/accounts").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": 1, "name": "acme"}], "page": 0, "size": 1, "total": 3}
        )
    )
    page = await invoke(
        rest_server,
        "wacm_standalone_account_list",
        page=0,
        size=1,
        status="ACTIVE",
        wasabi_account_number=100,
        company_name="Acme",
    )
    request = route.calls.last.request
    assert_basic_auth(request)
    assert dict(request.url.params) == {
        "page": "0",
        "size": "1",
        "status": "ACTIVE",
        "wasabiAccountNumber": "100",
        "companyName": "Acme",
    }
    assert page["items"] == [{"id": 1, "name": "acme"}]
    assert (page["next_token"], page["truncated"], page["total"]) == ("1", True, 3)
    assert_no_secrets(page)


@respx.mock
async def test_standalone_account_create_posts_the_body(rest_server):
    route = respx.post(f"{WACM}/v1/accounts").mock(
        return_value=httpx.Response(201, json={"data": {"id": 9, "email": "ada@example.com"}})
    )
    created = await invoke(
        rest_server, "wacm_standalone_account_create", **ACCOUNT_ARGS, company_name="Acme"
    )
    request = route.calls.last.request
    assert_basic_auth(request)
    assert body(request) == {
        "firstName": "Ada",
        "lastName": "Lovelace",
        "email": "ada@example.com",
        "country": "United Kingdom",
        "storageAmount": "1-5 TB",
        "companyName": "Acme",
    }
    assert created == {"data": {"id": 9, "email": "ada@example.com"}}


@respx.mock
async def test_standalone_account_create_refuses_when_read_only(rest_settings):
    route = respx.route(host="wacm.wasabi.test")
    server = read_only_rest_server(rest_settings)
    await assert_refused(server, "wacm_standalone_account_create", "read-only", **ACCOUNT_ARGS)
    assert route.call_count == 0


@respx.mock
async def test_standalone_account_create_reports_wacm_errors(rest_server):
    respx.post(f"{WACM}/v1/accounts").mock(
        return_value=httpx.Response(400, json={"error": "Email already in use", "code": 400})
    )
    await assert_refused(
        rest_server,
        "wacm_standalone_account_create",
        "HTTP 400: Email already in use",
        **ACCOUNT_ARGS,
    )


@pytest.mark.parametrize(
    ("tool", "path"),
    [
        ("wacm_storage_amount_list", "/v1/accounts/storage-amounts"),
        ("wacm_country_list", "/v1/accounts/countries"),
    ],
)
@respx.mock
async def test_reference_lists(rest_server, tool, path):
    route = respx.get(f"{WACM}{path}").mock(
        return_value=httpx.Response(200, json={"data": ["a", "b"]})
    )
    assert await invoke(rest_server, tool) == {"data": ["a", "b"]}
    assert_basic_auth(route.calls.last.request)


@respx.mock
async def test_control_account_list_never_returns_api_keys(rest_server, rest_settings):
    route = respx.get(f"{WACM}/v1/control-accounts").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"id": 3, "name": "ctl", "apiKey": rest_settings.wacm_api_key}],
                "total": 1,
            },
        )
    )
    page = await invoke(rest_server, "wacm_control_account_list")
    assert "includeApiKey" not in route.calls.last.request.url.params
    assert page["items"] == [{"id": 3, "name": "ctl"}]
    assert page["truncated"] is False
    assert_no_secrets(page)


@respx.mock
async def test_control_account_get(rest_server, rest_settings):
    route = respx.get(f"{WACM}/v1/control-accounts/3").mock(
        return_value=httpx.Response(
            200, json={"data": {"id": 3, "apiKey": rest_settings.wacm_api_key}}
        )
    )
    account = await invoke(rest_server, "wacm_control_account_get", control_account_id=3)
    assert account == {"data": {"id": 3}}
    assert "includeApiKey" not in route.calls.last.request.url.params


@respx.mock
async def test_control_account_get_reports_not_found(rest_server):
    respx.get(f"{WACM}/v1/control-accounts/404").mock(
        return_value=httpx.Response(404, json={"error": "Control account not found", "code": 404})
    )
    await assert_refused(
        rest_server,
        "wacm_control_account_get",
        "HTTP 404: Control account not found",
        control_account_id=404,
    )


@respx.mock
async def test_control_account_usage_list_sends_camel_case_filters(rest_server):
    route = respx.get(f"{WACM}/v1/control-accounts/usages").mock(
        return_value=httpx.Response(200, json={"data": [{"utilizationId": 11}]})
    )
    page = await invoke(
        rest_server,
        "wacm_control_account_usage_list",
        control_account_id=3,
        sub_account_id=None,
        latest=True,
        from_date="2026-11-01",
        to_date="2026-11-20",
    )
    assert dict(route.calls.last.request.url.params) == {
        "controlAccountId": "3",
        "latest": "true",
        "from": "2026-11-01",
        "to": "2026-11-20",
    }
    assert page["items"] == [{"utilizationId": 11}]


@respx.mock
async def test_control_account_usage_get(rest_server):
    respx.get(f"{WACM}/v1/control-accounts/usages/11").mock(
        return_value=httpx.Response(200, json={"data": {"utilizationId": 11}})
    )
    usage = await invoke(rest_server, "wacm_control_account_usage_get", utilization_id=11)
    assert usage == {"data": {"utilizationId": 11}}


@respx.mock
async def test_control_account_bucket_list(rest_server):
    route = respx.get(f"{WACM}/v1/control-accounts/3/buckets").mock(
        return_value=httpx.Response(
            200, json={"data": [{"name": "logs"}], "page": 0, "size": 10, "total": 1}
        )
    )
    page = await invoke(
        rest_server,
        "wacm_control_account_bucket_list",
        control_account_id=3,
        name="logs",
        bucket_number=4,
        region="us-east-1",
    )
    assert dict(route.calls.last.request.url.params) == {
        "name": "logs",
        "bucketNumber": "4",
        "region": "us-east-1",
    }
    assert page["items"] == [{"name": "logs"}]
    assert page["next_token"] is None

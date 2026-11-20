import httpx
import pytest
import respx

from tests.tools.wacm_support import WACM, assert_basic_auth, assert_refused, invoke

SUB_ACCOUNTS = {
    "data": [
        {"id": 1, "name": "alpha", "purchasedStorageTB": 10, "storageQuotaType": "Hard"},
        {"id": 2, "name": "beta", "purchasedStorageTB": 5, "storageQuotaType": "Soft"},
        {"id": 3, "name": "gamma", "purchasedStorageTB": 20, "storageQuotaType": "Hard"},
    ],
    "page": 0,
    "size": 100,
    "total": 3,
}
TB = 1024**4
USAGES = {
    "data": [
        {"utilizationId": 101, "subAccountId": 1, "activeStorage": int(9.5 * TB)},
        {"utilizationId": 102, "subAccountId": 2, "activeStorage": 1 * TB},
        {"utilizationId": 103, "subAccountId": 3, "activeStorage": int(18.5 * TB)},
    ],
    "page": 0,
    "size": 100,
    "total": 3,
}


@respx.mock
async def test_s7_sub_account_quotas_and_usage_show_who_is_near_quota(rest_server):
    accounts_route = respx.get(f"{WACM}/v1/sub-accounts").mock(
        return_value=httpx.Response(200, json=SUB_ACCOUNTS)
    )
    usages_route = respx.get(f"{WACM}/v1/usages").mock(
        return_value=httpx.Response(200, json=USAGES)
    )
    accounts = await invoke(rest_server, "wacm_sub_account_list", size=100)
    usages = await invoke(
        rest_server,
        "wacm_usage_list",
        size=100,
        latest=True,
        from_date="2026-11-01",
        to_date="2026-11-30",
    )
    assert_basic_auth(usages_route.calls.last.request)
    assert dict(usages_route.calls.last.request.url.params) == {
        "size": "100",
        "latest": "true",
        "from": "2026-11-01",
        "to": "2026-11-30",
    }
    assert accounts_route.call_count == 1
    assert accounts["truncated"] is False and usages["truncated"] is False

    used = {u["subAccountId"]: u["activeStorage"] / TB for u in usages["items"]}
    ratios = {a["name"]: used[a["id"]] / a["purchasedStorageTB"] for a in accounts["items"]}
    near_quota = sorted(name for name, ratio in ratios.items() if ratio >= 0.9)
    assert near_quota == ["alpha", "gamma"]


@respx.mock
async def test_invoice_list_sends_filters(rest_server):
    route = respx.get(f"{WACM}/v1/invoices").mock(
        return_value=httpx.Response(200, json={"data": [{"id": 900}], "totalPages": 1})
    )
    page = await invoke(
        rest_server,
        "wacm_invoice_list",
        governance_account_id=1,
        control_account_id=3,
        sub_account_id=42,
        invoice_id=900,
        wasabi_account_number=12345,
        latest=False,
    )
    assert dict(route.calls.last.request.url.params) == {
        "governanceAccountId": "1",
        "controlAccountId": "3",
        "subAccountId": "42",
        "invoiceId": "900",
        "wasabiAccountNumber": "12345",
        "latest": "false",
    }
    assert page["items"] == [{"id": 900}]
    assert page["next_token"] is None


@pytest.mark.parametrize(
    ("tool", "path", "arguments"),
    [
        ("wacm_invoice_get", "/v1/invoices/900", {"invoice_id": 900}),
        ("wacm_usage_get", "/v1/usages/101", {"utilization_id": 101}),
    ],
)
@respx.mock
async def test_get_tools(rest_server, tool, path, arguments):
    route = respx.get(f"{WACM}{path}").mock(
        return_value=httpx.Response(200, json={"data": {"id": 1}})
    )
    assert await invoke(rest_server, tool, **arguments) == {"data": {"id": 1}}
    assert_basic_auth(route.calls.last.request)


@respx.mock
async def test_invoice_get_reports_not_found(rest_server):
    respx.get(f"{WACM}/v1/invoices/1").mock(
        return_value=httpx.Response(404, json={"error": "Invoice not found", "code": 404})
    )
    await assert_refused(
        rest_server, "wacm_invoice_get", "HTTP 404: Invoice not found", invoice_id=1
    )


@respx.mock
async def test_usage_list_reports_wacm_errors(rest_server):
    respx.get(f"{WACM}/v1/usages").mock(
        return_value=httpx.Response(400, json={"error": "Invalid date range", "code": 400})
    )
    await assert_refused(
        rest_server, "wacm_usage_list", "HTTP 400: Invalid date range", from_date="2026-13-01"
    )

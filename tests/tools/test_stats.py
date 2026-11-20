import json

import httpx
import pytest
import respx
from mcp.server.mcpserver import MCPServer

from tests.conftest import FAKE, FAKE_ACCESS_KEY, FAKE_SECRET_KEY, SECRETS
from tests.helpers import ToolError, call
from wasabi_mcp.config import Settings
from wasabi_mcp.server import build_server

ACCOUNT = f"{FAKE['stats']}/v1/standalone/utilizations"
BUCKETS = f"{FAKE['stats']}/v1/standalone/utilizations/bucket"
AUTH = f"{FAKE_ACCESS_KEY}:{FAKE_SECRET_KEY}"
MISSING_KEYS = "Stats API needs WASABI_ACCESS_KEY_ID and WASABI_SECRET_ACCESS_KEY"

ACCOUNT_RECORD = {
    "StartTime": "2026-11-19T00:00:00Z",
    "EndTime": "2026-11-20T00:00:00Z",
    "NumBillableObjects": 1200,
    "PaddedStorageSizeBytes": 3 * 1024**3,
    "MeteredStorageSizeBytes": 3 * 1024**3,
    "DeletedStorageSizeBytes": 512 * 1024**2,
    "RawStorageSizeBytes": 3 * 1024**3 - 1000,
    "NumAPICalls": 4321,
    "UploadBytes": 2048,
    "DownloadBytes": 1024,
}


def bucket_record(name: str, padded: int, objects: int = 1) -> dict:
    return {
        "Bucket": name,
        "StartTime": "2026-11-19T00:00:00Z",
        "NumBillableObjects": objects,
        "PaddedStorageSizeBytes": padded,
        "DeletedStorageSizeBytes": 0,
    }


def no_secrets(result: object) -> None:
    text = json.dumps(result)
    for secret in SECRETS:
        assert secret not in text


@pytest.fixture
def no_keys_server(rest_settings: Settings) -> MCPServer:
    """Like ``rest_server``, but without the Wasabi key pair (``has_keys`` false)."""
    settings = Settings(
        region=rest_settings.region,
        read_only=False,
        s3_endpoint=FAKE["s3"],
        iam_endpoint=FAKE["iam"],
        sts_endpoint=FAKE["sts"],
        stats_endpoint=FAKE["stats"],
        wacm_endpoint=FAKE["wacm"],
    )
    assert not settings.has_keys
    return build_server(settings)


# --- paths, auth and parameters -------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "url"),
    [
        ("stats_account_utilization_list", ACCOUNT),
        ("stats_bucket_utilization_list", BUCKETS),
    ],
)
@respx.mock
async def test_list_tools_call_documented_path_with_key_pair(rest_server, tool, url):
    route = respx.get(url).mock(
        return_value=httpx.Response(
            200, json={"Records": [ACCOUNT_RECORD], "RecordCount": 1, "PageCount": 1}
        )
    )
    result = await call(
        rest_server,
        tool,
        from_date="2026-11-01",
        to_date="2026-11-19",
        latest=True,
        include_regional=False,
        page_num=0,
        page_size=50,
    )
    request = route.calls.last.request
    assert request.headers["authorization"] == AUTH
    assert dict(request.url.params) == {
        "from": "2026-11-01",
        "to": "2026-11-19",
        "latest": "true",
        "includeRegional": "false",
        "pageNum": "0",
        "pageSize": "50",
    }
    item = result["items"][0]
    assert item["PaddedStorageSizeBytes"] == 3 * 1024**3
    assert item["PaddedStorageSizeBytes_human"] == "3.0 GB"
    assert item["DeletedStorageSizeBytes_human"] == "512.0 MB"
    assert item["UploadBytes_human"] == "2.0 KB"
    assert "NumAPICalls_human" not in item
    no_secrets(result)


@pytest.mark.parametrize(
    "tool",
    ["stats_account_utilization_list", "stats_bucket_utilization_list"],
)
@respx.mock
async def test_list_tools_omit_none_parameters(rest_server, tool):
    route = respx.get(host="stats.wasabi.test").mock(
        return_value=httpx.Response(200, json={"Records": []})
    )
    await call(rest_server, tool, from_date="2026-11-01", latest=None, page_size=None)
    assert route.calls.last.request.url.query == b"from=2026-11-01"
    await call(rest_server, tool)
    assert route.calls.last.request.url.query == b""


@respx.mock
async def test_bucket_get_by_name_quotes_the_name(rest_server):
    route = respx.get(f"{BUCKETS}/my%20bucket").mock(
        return_value=httpx.Response(200, json={"Records": [bucket_record("my bucket", 10)]})
    )
    result = await call(rest_server, "stats_bucket_utilization_get", bucket="my bucket")
    request = route.calls.last.request
    assert request.headers["authorization"] == AUTH
    assert request.url.query == b""
    assert result["items"][0]["Bucket"] == "my bucket"


@respx.mock
async def test_bucket_get_by_number_with_parameters(rest_server):
    route = respx.get(f"{BUCKETS}/42").mock(
        return_value=httpx.Response(200, json={"Records": [], "RecordCount": 0, "PageCount": 0})
    )
    result = await call(
        rest_server,
        "stats_bucket_utilization_get",
        bucket_number=42,
        latest=True,
        from_date="2026-11-01",
        to_date=None,
    )
    request = route.calls.last.request
    assert request.headers["authorization"] == AUTH
    assert dict(request.url.params) == {"latest": "true", "from": "2026-11-01"}
    assert result["items"] == []
    assert result["next_page_num"] is None


# --- pagination ------------------------------------------------------------------------


@respx.mock
async def test_page_zero_exposes_counts_and_next_page(rest_server):
    respx.get(BUCKETS).mock(
        return_value=httpx.Response(
            200,
            json={
                "Records": [bucket_record("a", 1), bucket_record("b", 2)],
                "RecordCount": 5,
                "PageCount": 3,
            },
        )
    )
    result = await call(rest_server, "stats_bucket_utilization_list", page_num=0, page_size=2)
    assert {k: v for k, v in result.items() if k != "items"} == {
        "page_num": 0,
        "page_size": 2,
        "next_page_num": 1,
        "page_count": 3,
        "record_count": 5,
        "truncated": True,
    }


@respx.mock
async def test_later_page_carries_page_zero_counts_forward(rest_server):
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.params["pageNum"] == "0":
            records = [bucket_record("a", 1), bucket_record("b", 2)]
            return httpx.Response(200, json={"Records": records, "RecordCount": 5, "PageCount": 3})
        return httpx.Response(200, json={"Records": [bucket_record("c", 3)]})

    route = respx.get(BUCKETS).mock(side_effect=respond)
    middle = await call(
        rest_server,
        "stats_bucket_utilization_list",
        latest=True,
        page_num=1,
        page_size=2,
    )
    assert [c.request.url.params["pageNum"] for c in route.calls] == ["1", "0"]
    assert route.calls.last.request.url.params["latest"] == "true"
    assert middle["page_num"] == 1
    assert middle["record_count"] == 5
    assert middle["page_count"] == 3
    assert middle["next_page_num"] == 2

    last = await call(rest_server, "stats_bucket_utilization_list", page_num=2, page_size=2)
    assert last["page_count"] == 3
    assert last["next_page_num"] is None
    assert last["truncated"] is False


@respx.mock
async def test_bare_list_without_counts_falls_back_to_full_page(rest_server):
    respx.get(ACCOUNT).mock(return_value=httpx.Response(200, json=[ACCOUNT_RECORD]))
    full = await call(rest_server, "stats_account_utilization_list", page_size=1)
    assert full["page_count"] is None
    assert full["record_count"] is None
    assert full["next_page_num"] == 1
    assert full["truncated"] is True

    short = await call(rest_server, "stats_account_utilization_list", page_size=5)
    assert short["next_page_num"] is None
    assert short["truncated"] is False


# --- refusals --------------------------------------------------------------------------

ALL_TOOLS = [
    ("stats_account_utilization_list", {}),
    ("stats_bucket_utilization_list", {}),
    ("stats_bucket_utilization_get", {"bucket": "photos"}),
    ("stats_usage_summary", {}),
]


@pytest.mark.parametrize(("tool", "args"), ALL_TOOLS)
@respx.mock
async def test_missing_keys_refuse_without_request(no_keys_server, tool, args):
    route = respx.route(host="stats.wasabi.test")
    with pytest.raises(ToolError, match=MISSING_KEYS):
        await call(no_keys_server, tool, **args)
    assert route.call_count == 0
    assert respx.calls.call_count == 0


@pytest.mark.parametrize(
    ("tool", "args", "message"),
    [
        ("stats_account_utilization_list", {"from_date": "2026/11/01"}, "YYYY-MM-DD"),
        ("stats_account_utilization_list", {"to_date": "2026-02-30"}, "YYYY-MM-DD"),
        (
            "stats_bucket_utilization_list",
            {"from_date": "2026-11-19", "to_date": "2026-11-01"},
            "is after to_date",
        ),
        ("stats_bucket_utilization_list", {"page_size": 0}, "page_size must be between"),
        ("stats_bucket_utilization_list", {"page_size": 101}, "page_size must be between"),
        ("stats_account_utilization_list", {"page_num": -1}, "page_num must be 0 or more"),
        ("stats_bucket_utilization_get", {}, "exactly one of bucket"),
        ("stats_bucket_utilization_get", {"bucket": "a", "bucket_number": 1}, "exactly one"),
        ("stats_usage_summary", {"top_n": 0}, "top_n must be between"),
        ("stats_usage_summary", {"top_n": 101}, "top_n must be between"),
    ],
)
@respx.mock
async def test_invalid_input_refused_before_request(rest_server, tool, args, message):
    with pytest.raises(ToolError, match=message):
        await call(rest_server, tool, **args)
    assert respx.calls.call_count == 0


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize(("tool", "args"), ALL_TOOLS)
@respx.mock
async def test_denied_explains_required_permissions(rest_server, tool, args, status):
    respx.get(host="stats.wasabi.test").mock(
        return_value=httpx.Response(status, json={"message": "Access denied"})
    )
    with pytest.raises(ToolError) as excinfo:
        await call(rest_server, tool, **args)
    text = str(excinfo.value)
    assert f"HTTP {status}" in text
    assert "root" in text
    assert "WasabiAccountStatsAccess" in text
    assert "WasabiBucketStatsAccess" in text
    for secret in SECRETS:
        assert secret not in text


@respx.mock
async def test_other_errors_pass_through(rest_server):
    respx.get(ACCOUNT).mock(return_value=httpx.Response(500, json={"message": "boom"}))
    with pytest.raises(ToolError, match="HTTP 500: boom") as excinfo:
        await call(rest_server, "stats_account_utilization_list")
    assert "WasabiAccountStatsAccess" not in str(excinfo.value)


# --- summary and scenario S1 -----------------------------------------------------------


@respx.mock
async def test_usage_summary_totals_and_top_buckets(rest_server):
    account = respx.get(ACCOUNT).mock(
        return_value=httpx.Response(200, json={"Records": [ACCOUNT_RECORD]})
    )
    buckets = respx.get(BUCKETS).mock(
        return_value=httpx.Response(
            200,
            json={
                "Records": [
                    bucket_record("small", 10, 1),
                    bucket_record("big", 3000, 7),
                    bucket_record("mid", 500, 3),
                ],
                "RecordCount": 3,
                "PageCount": 1,
            },
        )
    )
    result = await call(rest_server, "stats_usage_summary", top_n=2)
    assert account.calls.last.request.url.params["latest"] == "true"
    assert dict(buckets.calls.last.request.url.params) == {
        "latest": "true",
        "pageNum": "0",
        "pageSize": "100",
    }
    assert account.calls.last.request.headers["authorization"] == AUTH
    assert result["date"] == "2026-11-19T00:00:00Z"
    assert result["active_storage_bytes"] == 3 * 1024**3
    assert result["active_storage_bytes_human"] == "3.0 GB"
    assert result["deleted_storage_bytes"] == 512 * 1024**2
    assert result["objects"] == 1200
    assert result["api_calls"] == 4321
    assert result["upload_bytes"] == 2048
    assert result["download_bytes"] == 1024
    assert result["bucket_count"] == 3
    assert [b["bucket"] for b in result["top_buckets"]] == ["big", "mid"]
    assert result["top_buckets"][0]["active_storage_bytes"] == 3000
    assert result["truncated"] is False
    no_secrets(result)


@respx.mock
async def test_usage_summary_stops_at_page_cap(rest_server):
    respx.get(ACCOUNT).mock(return_value=httpx.Response(200, json={"Records": [ACCOUNT_RECORD]}))

    def respond(request: httpx.Request) -> httpx.Response:
        number = int(request.url.params["pageNum"])
        records = [bucket_record(f"b{number}-{i}", number * 100 + i) for i in range(100)]
        body = {"Records": records}
        if number == 0:
            body |= {"RecordCount": 1500, "PageCount": 15}
        return httpx.Response(200, json=body)

    buckets = respx.get(BUCKETS).mock(side_effect=respond)
    result = await call(rest_server, "stats_usage_summary", top_n=1)
    assert buckets.call_count == 10
    assert result["bucket_count"] == 1000
    assert result["truncated"] is True
    assert result["top_buckets"][0]["bucket"] == "b9-99"


@respx.mock
async def test_scenario_s1_per_bucket_stored_bytes(rest_server):
    # Stand-in for bucket_list output: buckets.py isn't part of this change.
    bucket_names = ["photos", "backups", "empty"]
    respx.get(BUCKETS).mock(
        return_value=httpx.Response(
            200,
            json={
                "Records": [
                    bucket_record("photos", 5 * 1024**2, 40),
                    bucket_record("backups", 2 * 1024**3, 9),
                ],
                "RecordCount": 2,
                "PageCount": 1,
            },
        )
    )
    stats = await call(rest_server, "stats_bucket_utilization_list", latest=True)
    by_name = {item["Bucket"]: item for item in stats["items"]}
    report = {
        name: {
            "stored_bytes": by_name.get(name, {}).get("PaddedStorageSizeBytes", 0),
            "stored": by_name.get(name, {}).get("PaddedStorageSizeBytes_human", "0 B"),
        }
        for name in bucket_names
    }
    assert report == {
        "photos": {"stored_bytes": 5 * 1024**2, "stored": "5.0 MB"},
        "backups": {"stored_bytes": 2 * 1024**3, "stored": "2.0 GB"},
        "empty": {"stored_bytes": 0, "stored": "0 B"},
    }
    no_secrets(stats)


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (httpx.ConnectError("[Errno 111] Connection refused"), "couldn't connect to"),
        (httpx.ConnectError("[Errno -2] Name or service not known"), "couldn't resolve host"),
        (httpx.ReadTimeout("timed out"), "timed out reading from"),
    ],
)
@respx.mock
async def test_network_errors_reach_the_client_with_operation_host_and_reason(
    rest_server, error, reason
):
    respx.route(host="stats.wasabi.test").mock(side_effect=error)
    with pytest.raises(ToolError) as excinfo:
        await call(rest_server, "stats_account_utilization_list")
    text = str(excinfo.value)
    assert f"Stats API GET /v1/standalone/utilizations failed: {reason} stats.wasabi.test" in text
    for secret in (*SECRETS, FAKE_ACCESS_KEY):
        assert secret not in text

import httpx
import pytest
import respx

from tests.tools.wacm_support import WACM
from wasabi_mcp.safety import ToolRefused
from wasabi_mcp.tools import _wacm


@pytest.fixture
def sleeps(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(_wacm, "_sleep", calls.append)
    return calls


@respx.mock
def test_call_retries_once_after_429(rest_settings, sleeps):
    route = respx.get(f"{WACM}/v1/members").mock(
        side_effect=[
            httpx.Response(429, json={"error": "Too many requests", "code": 429}),
            httpx.Response(200, json={"data": []}),
        ]
    )
    assert _wacm.call(rest_settings, "GET", "/v1/members") == {"data": []}
    assert route.call_count == 2
    assert sleeps == [_wacm.RETRY_DELAY]


@respx.mock
def test_call_reports_a_second_429(rest_settings, sleeps):
    route = respx.get(f"{WACM}/v1/members").mock(
        return_value=httpx.Response(429, json={"error": "Too many requests", "code": 429})
    )
    with pytest.raises(ToolRefused, match="HTTP 429: Too many requests"):
        _wacm.call(rest_settings, "GET", "/v1/members")
    assert route.call_count == 2
    assert sleeps == [_wacm.RETRY_DELAY]


@respx.mock
def test_call_does_not_retry_other_errors(rest_settings, sleeps):
    route = respx.get(f"{WACM}/v1/members/9").mock(
        return_value=httpx.Response(404, json={"error": "Member not found", "code": 404})
    )
    with pytest.raises(ToolRefused, match=r"HTTP 404: Member not found$"):
        _wacm.call(rest_settings, "GET", "/v1/members/9")
    assert route.call_count == 1
    assert sleeps == []


def test_page_result_maps_the_envelope_and_keeps_paging_fields():
    response = {"data": [{"id": 1}, {"id": 2}], "page": 0, "size": 2, "total": 5}
    assert _wacm.page_result(response, 0, 2) == {
        "page": 0,
        "size": 2,
        "total": 5,
        "items": [{"id": 1}, {"id": 2}],
        "next_token": "1",
        "truncated": True,
    }


def test_page_result_on_the_last_page():
    response = {"data": [{"id": 5}], "page": 2, "size": 2, "total": 5}
    page = _wacm.page_result(response, 2, 2)
    assert page["items"] == [{"id": 5}]
    assert (page["next_token"], page["truncated"]) == (None, False)


def test_page_result_uses_total_pages():
    page = _wacm.page_result({"content": [{"id": 1}], "totalPages": 3}, 1, None)
    assert (page["next_token"], page["truncated"]) == ("2", True)
    assert page["totalPages"] == 3


def test_page_result_without_counts_guesses_from_a_full_page():
    assert _wacm.page_result([{"id": 1}, {"id": 2}], None, 2)["next_token"] == "1"
    assert _wacm.page_result([{"id": 1}], None, 2)["truncated"] is False
    assert _wacm.page_result({"data": []}, None, None) == {
        "items": [],
        "next_token": None,
        "truncated": False,
    }


def test_scrub_drops_key_fields_at_any_depth():
    response = {
        "id": 7,
        "apiKey": "k",
        "keys": [{"accessKey": "AKIA", "secretAccessKey": "s", "secret_key": "s"}],
        "owner": {"name": "x", "password": "p", "api-key": "k"},
    }
    assert _wacm.scrub(response) == {
        "id": 7,
        "keys": [{"accessKey": "AKIA"}],
        "owner": {"name": "x"},
    }


def test_page_result_scrubs_items():
    page = _wacm.page_result({"data": [{"id": 1, "apiKey": "k"}]}, None, None)
    assert page["items"] == [{"id": 1}]


def test_compact_drops_none():
    assert _wacm.compact(a=1, b=None, c=False) == {"a": 1, "c": False}

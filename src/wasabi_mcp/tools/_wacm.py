"""Shared WACM Connect plumbing for the ``wacm_*`` tool groups.

The leading underscore keeps ``discover_groups`` from loading this as a tool group.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from wasabi_mcp import rest
from wasabi_mcp.config import Settings
from wasabi_mcp.safety import ToolRefused
from wasabi_mcp.tools import clean

# ``rest.wacm_request`` doesn't expose the Retry-After header, so the PRD default is used.
RETRY_DELAY = 5.0
_sleep = time.sleep

# Field names (lower-cased, without ``_`` or ``-``) that carry keys or passwords.
SECRET_FIELDS = frozenset(
    {
        "apikey",
        "apikeys",
        "secretaccesskey",
        "secretkey",
        "accesskeysecret",
        "secret",
        "password",
    }
)

# Envelope fields that hold a list response's items, in the order they're tried.
_ITEM_FIELDS = ("data", "items", "content", "results")
_TOTAL_FIELDS = ("total", "totalCount", "totalElements", "totalRecords", "count")
_PAGES_FIELDS = ("totalPages", "pages", "pageCount")
_PAGE_FIELDS = ("page", "pageNumber", "currentPage")


def call(
    settings: Settings,
    method: str,
    path: str,
    *,
    params: Mapping[str, Any] | None = None,
    body: Any = None,
) -> Any:
    """``rest.wacm_request``, retried once after ``RETRY_DELAY`` seconds on HTTP 429."""
    try:
        return rest.wacm_request(settings, method, path, params=params, body=body)
    except ToolRefused as exc:
        if "HTTP 429" not in str(exc):
            raise
    _sleep(RETRY_DELAY)
    return rest.wacm_request(settings, method, path, params=params, body=body)


def compact(**fields: Any) -> dict[str, Any]:
    """A JSON body of the given fields, without those set to ``None``."""
    return {k: v for k, v in fields.items() if v is not None}


def _normalise(name: str) -> str:
    return name.lower().replace("_", "").replace("-", "")


def scrub(value: Any) -> Any:
    """Drop every API-key, secret-key and password field, at any depth."""
    if isinstance(value, Mapping):
        return {k: scrub(v) for k, v in value.items() if _normalise(str(k)) not in SECRET_FIELDS}
    if isinstance(value, list | tuple):
        return [scrub(item) for item in value]
    return value


def result(response: Any) -> Any:
    """A single WACM response, JSON-ready and without secrets."""
    return clean(scrub(response))


def _first(payload: Mapping[str, Any], fields: tuple[str, ...]) -> Any:
    return next((payload[f] for f in fields if payload.get(f) is not None), None)


def page_result(response: Any, page: int | None, size: int | None) -> dict[str, Any]:
    """Map WACM's page envelope to ``{"items", "next_token", "truncated"}``.

    Pages are numbered from 0. ``next_token`` is the next page number as a string, or
    ``None`` on the last page.
    WACM's own paging fields (such as the total count) are kept alongside.
    """
    if isinstance(response, list):
        payload: dict[str, Any] = {}
        items = response
    elif isinstance(response, Mapping):
        payload = dict(response)
        field = next((f for f in _ITEM_FIELDS if isinstance(payload.get(f), list)), None)
        items = payload.pop(field) if field else []
    else:
        payload, items = {}, []

    current = _first(payload, _PAGE_FIELDS)
    current = int(current) if current is not None else (page or 0)
    per_page = _first(payload, ("size", "pageSize", "limit"))
    per_page = int(per_page) if per_page is not None else size
    total = _first(payload, _TOTAL_FIELDS)
    pages = _first(payload, _PAGES_FIELDS)

    # WACM pages are numbered from 0.
    if pages is not None:
        truncated = current + 1 < int(pages)
    elif total is not None and per_page:
        truncated = (current + 1) * per_page < int(total)
    else:
        truncated = bool(per_page) and len(items) >= per_page

    extra = {k: v for k, v in payload.items() if k not in ("items", "next_token", "truncated")}
    return result(
        {
            **extra,
            "items": items,
            "next_token": str(current + 1) if truncated else None,
            "truncated": truncated,
        }
    )

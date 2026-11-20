"""Usage from the Wasabi Stats API (PRD section 6.13).

Stats are computed daily at 00:00 UTC. Storage fields are point-in-time; byte and call
counters are per day. Padded size applies the 4 KB minimum object size, and deleted
storage reflects minimum-retention billing. The keys must be root, or a sub-user with
WasabiAccountStatsAccess or WasabiBucketStatsAccess.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any
from urllib.parse import quote

from mcp.server.mcpserver import MCPServer

from wasabi_mcp import rest
from wasabi_mcp.config import Settings
from wasabi_mcp.safety import ToolRefused
from wasabi_mcp.tools import READ, ToolContext, clean

ACCOUNT_PATH = "/v1/standalone/utilizations"
BUCKET_PATH = "/v1/standalone/utilizations/bucket"
MAX_PAGE_SIZE = 100
MAX_TOP_N = 100
SUMMARY_PAGE_SIZE = 100
SUMMARY_MAX_PAGES = 10

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_UNITS = ("B", "KB", "MB", "GB", "TB", "PB", "EB")
_PERMISSION_HINT = (
    "the Stats API needs the root account's keys, or a sub-user's keys with the "
    "WasabiAccountStatsAccess or WasabiBucketStatsAccess policy attached"
)


def _date(name: str, value: str | None) -> date | None:
    """Parse a YYYY-MM-DD parameter, refusing anything else."""
    if value is None:
        return None
    try:
        if not _DATE.fullmatch(value):
            raise ValueError(value)
        return date.fromisoformat(value)
    except ValueError:
        raise ToolRefused(f"{name} must be a date in YYYY-MM-DD form, got {value!r}") from None


def _check_range(from_date: str | None, to_date: str | None) -> None:
    start, end = _date("from_date", from_date), _date("to_date", to_date)
    if start and end and start > end:
        raise ToolRefused(f"from_date {from_date} is after to_date {to_date}")


def _check_page(page_num: int | None, page_size: int | None) -> None:
    if page_num is not None and page_num < 0:
        raise ToolRefused(f"page_num must be 0 or more, got {page_num}")
    if page_size is not None and not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ToolRefused(f"page_size must be between 1 and {MAX_PAGE_SIZE}, got {page_size}")


def _check_bucket(bucket: str | None, bucket_number: int | None) -> None:
    if (bucket is None) == (bucket_number is None):
        raise ToolRefused("Give exactly one of bucket (name) or bucket_number")
    if bucket is not None and not bucket:
        raise ToolRefused("bucket must not be empty")


def _check_top_n(top_n: int) -> None:
    if not 1 <= top_n <= MAX_TOP_N:
        raise ToolRefused(f"top_n must be between 1 and {MAX_TOP_N}, got {top_n}")


def _query(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    latest: bool | None = None,
    include_regional: bool | None = None,
    page_num: int | None = None,
    page_size: int | None = None,
) -> dict[str, str | int | None]:
    """Wasabi's query names; ``None`` values are left for ``stats_get`` to drop."""

    def flag(value: bool | None) -> str | None:
        return None if value is None else str(value).lower()

    return {
        "from": from_date,
        "to": to_date,
        "latest": flag(latest),
        "includeRegional": flag(include_regional),
        "pageNum": page_num,
        "pageSize": page_size,
    }


def _get(settings: Settings, path: str, params: Mapping[str, Any]) -> Any:
    """``rest.stats_get``, with a 401/403 explained in terms of the required permissions."""
    try:
        return rest.stats_get(settings, path, params)
    except ToolRefused as exc:
        message = str(exc)
        if "HTTP 401" in message or "HTTP 403" in message:
            raise ToolRefused(f"{message}. Access denied: {_PERMISSION_HINT}.") from exc
        raise


def _human(n: int | float) -> str:
    """A byte count in binary units: ``1536`` -> ``"1.5 KB"``."""
    size = float(n)
    for unit in _UNITS:
        if abs(size) < 1024 or unit == _UNITS[-1]:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _record(rec: Any) -> Any:
    """A cleaned record with a ``<field>_human`` sibling for each numeric ``*Bytes`` field."""
    cleaned = clean(rec)
    if not isinstance(cleaned, dict):
        return cleaned
    extra = {
        f"{key}_human": _human(value)
        for key, value in cleaned.items()
        if key.endswith("Bytes") and _is_number(value)
    }
    return {**cleaned, **extra}


def _records(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return list(payload.get("Records") or [])
    return []


def _counts(payload: Any) -> tuple[int | None, int | None]:
    """(RecordCount, PageCount) when Wasabi sent them (page 0 only)."""
    if not isinstance(payload, dict):
        return None, None
    record_count, page_count = payload.get("RecordCount"), payload.get("PageCount")
    return (
        record_count if _is_number(record_count) else None,
        page_count if _is_number(page_count) else None,
    )


def _page(
    payload: Any,
    page_num: int | None,
    page_size: int | None,
    totals: tuple[int | None, int | None] = (None, None),
) -> dict[str, Any]:
    """Normalise a Stats page; ``totals`` are page 0's (RecordCount, PageCount)."""
    items = [_record(rec) for rec in _records(payload)]
    number = page_num or 0
    record_count, page_count = _counts(payload)
    record_count = record_count if record_count is not None else totals[0]
    page_count = page_count if page_count is not None else totals[1]
    if page_count is not None:
        has_more = number + 1 < page_count
    else:
        # Without counts, a full page may have more after it.
        has_more = page_size is not None and len(items) >= page_size
    next_page_num = number + 1 if has_more else None
    return {
        "items": items,
        "page_num": number,
        "page_size": page_size,
        "next_page_num": next_page_num,
        "page_count": page_count,
        "record_count": record_count,
        "truncated": next_page_num is not None,
    }


def _paged(
    fetch: Callable[[int | None], Any], page_num: int | None, page_size: int | None
) -> dict[str, Any]:
    """Fetch ``page_num``; past page 0, also fetch page 0 to carry its counts forward."""
    payload = fetch(page_num)
    totals: tuple[int | None, int | None] = (None, None)
    if page_num and _counts(payload) == (None, None):
        totals = _counts(fetch(0))
    return _page(payload, page_num, page_size, totals)


def _total(records: list[dict[str, Any]], field: str) -> int | float:
    return sum(rec[field] for rec in records if _is_number(rec.get(field)))


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings

    def list_page(
        path: str,
        from_date: str | None,
        to_date: str | None,
        latest: bool | None,
        include_regional: bool | None,
        page_num: int | None,
        page_size: int | None,
    ) -> dict[str, Any]:
        _check_range(from_date, to_date)
        _check_page(page_num, page_size)

        def fetch(number: int | None) -> Any:
            params = _query(
                from_date=from_date,
                to_date=to_date,
                latest=latest,
                include_regional=include_regional,
                page_num=number,
                page_size=page_size,
            )
            return _get(settings, path, params)

        return _paged(fetch, page_num, page_size)

    @server.tool(name="stats_account_utilization_list", annotations=READ)
    def stats_account_utilization_list(
        from_date: str | None = None,
        to_date: str | None = None,
        latest: bool | None = None,
        include_regional: bool | None = None,
        page_num: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        """List daily account utilization records from the Stats API (dates YYYY-MM-DD;
        Wasabi defaults to the last month; page_size at most 100), with *_human sizes and
        page fields. Stats are computed daily at 00:00 UTC; storage is point-in-time,
        byte and call counters are per day; padded size applies the 4 KB minimum object
        size; deleted storage reflects minimum-retention billing."""
        return list_page(
            ACCOUNT_PATH, from_date, to_date, latest, include_regional, page_num, page_size
        )

    @server.tool(name="stats_bucket_utilization_list", annotations=READ)
    def stats_bucket_utilization_list(
        from_date: str | None = None,
        to_date: str | None = None,
        latest: bool | None = None,
        include_regional: bool | None = None,
        page_num: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        """List daily per-bucket utilization records from the Stats API (dates YYYY-MM-DD;
        page_size at most 100), with *_human sizes and page fields; record and page counts
        are carried forward from page 0. Stats are computed daily at 00:00 UTC; storage is
        point-in-time, byte and call counters are per day; padded size applies the 4 KB
        minimum object size; deleted storage reflects minimum-retention billing."""
        return list_page(
            BUCKET_PATH, from_date, to_date, latest, include_regional, page_num, page_size
        )

    @server.tool(name="stats_bucket_utilization_get", annotations=READ)
    def stats_bucket_utilization_get(
        bucket: str | None = None,
        bucket_number: int | None = None,
        latest: bool | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        page_num: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        """Get daily utilization records for one bucket, by name or by bucket number
        (exactly one), with *_human sizes and page fields. Stats are computed daily at
        00:00 UTC; storage is point-in-time, byte and call counters are per day; padded
        size applies the 4 KB minimum object size; deleted storage reflects
        minimum-retention billing."""
        _check_bucket(bucket, bucket_number)
        key = quote(bucket, safe="") if bucket is not None else str(bucket_number)
        return list_page(
            f"{BUCKET_PATH}/{key}", from_date, to_date, latest, None, page_num, page_size
        )

    @server.tool(name="stats_usage_summary", annotations=READ)
    def stats_usage_summary(top_n: int = 10) -> dict[str, Any]:
        """Summarise the latest day of usage in one call: total active and deleted storage,
        objects, API calls, upload and download bytes, and the top_n buckets by active
        (padded) storage. Stats are computed daily at 00:00 UTC; storage is point-in-time,
        byte and call counters are per day; deleted storage reflects minimum-retention
        billing."""
        _check_top_n(top_n)
        account = _records(_get(settings, ACCOUNT_PATH, _query(latest=True)))
        accounts = [rec for rec in account if isinstance(rec, dict)]

        buckets: list[dict[str, Any]] = []
        totals: tuple[int | None, int | None] = (None, None)
        next_page: int | None = 0
        while next_page is not None and next_page < SUMMARY_MAX_PAGES:
            params = _query(latest=True, page_num=next_page, page_size=SUMMARY_PAGE_SIZE)
            payload = _get(settings, BUCKET_PATH, params)
            if next_page == 0:
                totals = _counts(payload)
            page = _page(payload, next_page, SUMMARY_PAGE_SIZE, totals)
            buckets.extend(rec for rec in page["items"] if isinstance(rec, dict))
            next_page = page["next_page_num"]
        truncated = next_page is not None

        sizes = {
            "active_storage_bytes": _total(accounts, "PaddedStorageSizeBytes"),
            "deleted_storage_bytes": _total(accounts, "DeletedStorageSizeBytes"),
            "upload_bytes": _total(accounts, "UploadBytes"),
            "download_bytes": _total(accounts, "DownloadBytes"),
        }
        top = sorted(
            buckets,
            key=lambda rec: rec.get("PaddedStorageSizeBytes") or 0,
            reverse=True,
        )[:top_n]
        return {
            "date": next((rec.get("StartTime") for rec in accounts if rec.get("StartTime")), None),
            **sizes,
            **{f"{key}_human": _human(value) for key, value in sizes.items()},
            "objects": _total(accounts, "NumBillableObjects"),
            "api_calls": _total(accounts, "NumAPICalls"),
            "bucket_count": len(buckets),
            "top_buckets": [
                {
                    "bucket": rec.get("Bucket"),
                    "active_storage_bytes": rec.get("PaddedStorageSizeBytes") or 0,
                    "active_storage_human": _human(rec.get("PaddedStorageSizeBytes") or 0),
                    "objects": rec.get("NumBillableObjects"),
                }
                for rec in top
            ],
            "truncated": truncated,
        }

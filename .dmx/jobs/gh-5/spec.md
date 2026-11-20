---
ticket: gh-5
branch: feature-gh-5-add-stats-api-utilization-and-usage
summary: Add Stats API utilization and usage tools
ticketing: github-issues
---

# Add Stats API utilization and usage tools

**Ticket:** [#5](https://github.com/deepmodel-labs/wasabi-mcp-factory-demo/issues/5)
**Type:** feature
**Branch:** feature-gh-5-add-stats-api-utilization-and-usage

---

## Context
The foundation (PRD section 4) is on `main`, but no usage tools exist yet, so an agent can't answer "how much am I storing?" (scenario S1). This is ticket 19 of PRD section 9 (item WMCP-6, T5 of 7 in WMCP-B2): the four Stats API tools of PRD section 6.13, built on the existing `rest.stats_get` helper.

## Scope
- New module `src/wasabi_mcp/tools/stats.py` with `register(server, ctx)`, copying the shape of `tools/meta.py`. All four tools are kind R (`READ` annotation, no guards):
  - `stats_account_utilization_list`: `GET /v1/standalone/utilizations`. Params `from_date`, `to_date` (YYYY-MM-DD), `latest`, `include_regional`, `page_num`, `page_size` (1–100). Records as Wasabi returns them, plus `*_human` sizes for storage fields.
  - `stats_bucket_utilization_list`: `GET /v1/standalone/utilizations/bucket`. Same date-range and page parameters. Per-bucket records. `RecordCount` and `PageCount` come only on page 0; carry them forward.
  - `stats_bucket_utilization_get`: `GET /v1/standalone/utilizations/bucket/{bucketName}` or `.../{bucketNumber}`. Takes `bucket` or `bucket_number` (exactly one), `latest`, date range, page params.
  - `stats_usage_summary`: from the two lists above, the latest day: total active storage, deleted storage, objects, API calls, upload and download bytes, and the top `top_n` buckets by active storage.
- Pagination fields in the result of each list/get tool: `items`, `page_num`, `page_size`, `next_page_num` (null on the last page), `page_count`, `record_count`, `truncated`.
- Input validation before any request: date format and `from_date <= to_date`, `page_size` bounds, `page_num >= 0`, exactly one of `bucket` / `bucket_number`, `top_n` bounds.
- A 401/403 from `stats_get` is re-raised as a `ToolRefused` explaining that the keys must be root, or a sub-user with WasabiAccountStatsAccess / WasabiBucketStatsAccess.
- Docstrings carry the metric notes from 6.13.
- `tests/tools/test_stats.py` (respx with `rest_settings` / `rest_server`, `tests.helpers.call`) and `docs/tools/stats.md`.

## Out of Scope
- `bucket_list` itself (ticket 1, `buckets.py`, not on `main` yet). The S1 test composes bucket names with stats data without importing `buckets.py`.
- Live tests (ticket 24 adds the live suite).
- Any change to shared files: `rest.py`, `server.py`, `conftest.py`, `helpers.py`, `.github/`, `docs/tools.md`, README.
- WACM usage (`wacm_usage_list`, ticket 23).

## Technical Approach
- **Layer:** a tool-group module only, auto-discovered by `tools/__init__.py`. Calls go through `rest.stats_get(settings, path, params)`, which already raises the missing-keys `ToolRefused` before any request, drops `None` params and sends `Authorization: <key>:<secret>`.
- **Query mapping:** snake_case tool params map to Wasabi's camelCase query (`from`, `to`, `latest`, `includeRegional`, `pageNum`, `pageSize`); the exact names come from Wasabi's Stats API docs, checked during plan. Booleans are sent only when not None.
- **Response shaping:** a small `_page(payload, page_num, page_size)` helper normalises Wasabi's `{Records, RecordCount, PageCount}` (or a bare list) into the page dict above; records are passed through `clean()` with `*_human` siblings added for storage fields (`PaddedStorageSizeBytes`, `MeteredStorageSizeBytes`, `DeletedStorageSizeBytes`, `RawStorageSizeBytes`, etc.) using the foundation's human-size helper if one exists, else a local one.
- **Page-0 totals:** `RecordCount` / `PageCount` are read on page 0. For `page_num > 0` the tool fetches page 0 first (same filters) to carry them forward; `next_page_num` falls back to "full page ⇒ maybe more" when the counts are absent.
- **Summary:** `stats_usage_summary` calls the account list with `latest=true` and the bucket list with `latest=true`, paging the bucket list up to a bounded number of pages, and sums/sorts in memory.
- **Errors:** wrap `stats_get` and catch `ToolRefused` whose message contains `HTTP 401`/`HTTP 403`, re-raising with the permission explanation.
- **Tests:** respx routes on `FAKE["stats"]`; assert path, the `Authorization` header and absent query keys; a no-keys settings variant (keys `None`) asserts the refusal and `respx` call count 0 for every tool; S1 test mocks the bucket stats list and joins with a bucket-name list to give per-bucket stored bytes; `SECRETS` never appear in outputs.

---

## Questions
<!-- Answer each question before running /dmx/plan. -->

1. How does the S1 unit test use `bucket_list` when `buckets.py` (ticket 1) isn't on `main` and tickets may not depend on each other?
   Answer: The test composes the scenario from what this ticket owns: it lists buckets through moto/boto3 (or a fixed list of names standing in for `bucket_list` output) and joins them with `stats_bucket_utilization_list` items by bucket name, asserting per-bucket stored bytes. If `bucket_list` is registered on the server, the test may call it; otherwise it uses the stand-in. No import of `buckets.py`.

2. Does `stats_bucket_utilization_list` use the `{"items", "next_token", "truncated"}` list shape from PRD 5.4, or page numbers?
   Answer: The Stats API is page-number based, so results keep `items` and `truncated` from 5.4 and add `page_num`, `page_size`, `next_page_num`, `page_count` and `record_count` (the acceptance criterion asks for page and next-page fields). `max_items`/`continuation_token` don't apply.

3. Which storage fields get `*_human` siblings?
   Answer: Every numeric field in a record whose name ends in `SizeBytes` or `Bytes` and describes storage (point-in-time fields); the per-day byte counters (`*BytesUploaded`/`*BytesDownloaded`) get them too where cheap, since PRD 5.4 allows "where useful". Exact field names are confirmed against Wasabi's documented response during plan.

4. How many bucket pages may `stats_usage_summary` fetch?
   Answer: It requests `page_size=100` with `latest=true` and stops at `PageCount` or a cap of 10 pages (1,000 buckets), reporting `truncated: true` if the cap was hit.

5. Where does the no-keys test get settings with `has_keys` false, given `conftest.py` can't be edited?
   Answer: A local fixture in `tests/tools/test_stats.py` builds `Settings` like `rest_settings` but without access keys, and a server from it the same way `rest_server` does.

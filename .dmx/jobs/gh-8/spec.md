---
ticket: gh-8
branch: feature-gh-8-add-wasabi-s3-extension-tools-for
summary: Add Wasabi S3 extension tools for objects and compliance
ticketing: github-issues
---

# Add Wasabi S3 extension tools for objects and compliance

**Ticket:** [#8](https://github.com/deepmodel-labs/wasabi-mcp-factory-demo/issues/8)
**Type:** feature
**Branch:** feature-gh-8-add-wasabi-s3-extension-tools-for

---

## Context
The foundation (PRD section 4, merged in #2) provides `rest.signed_s3`, which sends path-style, SigV4-signed S3 requests with any method (including `MOVE`) and bare subresource queries. No tool uses it yet for Wasabi's object-level extensions or Wasabi Compliance. This item is WMCP-4, **T3 of 7** of WMCP-B2 (#3). It delivers PRD tickets 4 and 9 (sections 6.4 and 6.9).

## Scope
Two new tool-group modules, each with `tests/tools/test_<module>.py` and `docs/tools/<module>.md` (PRD 5.1, 5.7, 8.7):

- `src/wasabi_mcp/tools/objects_ext.py`, 4 tools (6.4):
  - `object_rename` (D): `MOVE /bucket/key`, with a `Destination` header and `Overwrite: true|false` from `overwrite: bool = False`.
  - `object_rename_prefix` (D): `MOVE /bucket/prefix` with `X-Wasabi-Prefix: true`. Covers all versions. Returns the per-key results parsed from Wasabi's XML.
  - `object_compose` (W): `PUT /bucket/key?compose`, linking 1–32 source keys in the same bucket. No SSE-C. The description says the ETag is a hash of the part MD5s.
  - `object_append` (W): `PUT /bucket/key?append` with text of at most 1 MiB. At most 1,023 appends per object.
- `src/wasabi_mcp/tools/compliance.py`, 4 tools (6.9):
  - `bucket_compliance_get` (R): `GET /bucket?compliance`, returning Status, LockTime, RetentionDays, ConditionalHold, IsLocked and DeleteAfterRetention as snake_case JSON.
  - `bucket_compliance_set` (D): `PUT /bucket?compliance` with `enabled`, `retention_days` (required), `conditional_hold`, `lock_time` (an ISO date, `now` or `off`) and `delete_after_retention`. The description says locked settings can't be reduced without Wasabi Support. It surfaces `ComplianceSettingsLocked`.
  - `object_compliance_get` (R): `GET /bucket/key?compliance`, including the SHA256, which Wasabi doesn't return for multipart or composed objects.
  - `object_compliance_set` (D): `PUT /bucket/key?compliance`:
    - `release_conditional_hold` can only release;
    - `legal_hold` is a bool;
    - `retention_time` can only extend. It is checked against the current setting (GET first), and a shorter value is refused before the PUT.
- PRD section 5 throughout:
  - catalogue names and kinds, with `READ` / `WRITE` / `DESTRUCTIVE` annotations;
  - `require_writable` / `require_confirmed` before any network call;
  - `confirm: bool = False` on D tools;
  - `clean()` output;
  - `rest.signed_s3` errors propagate as `ToolRefused("<Action> failed: <Code>: <Message>")`.

## Out of Scope
- **The other bucket-level extensions named in WMCP-4** are owned by T1. They are already implemented, with respx tests, on branch `feature-gh-3-add-s3-bucket-and-bucket-configuration` (spec `.dmx/jobs/gh-3`):
  - `bucket_delete` with `force=True` (`DELETE ?force_delete=true`) and `bucket_rename` (`MOVE /bucket`), both in `buckets.py`;
  - `bucket_inventory_list/get/set/delete` (`?inventory`, `?inventory&id=`) in `bucket_ops.py`;
  - `bucket_replication_backfill` (`PUT ?manifestCreateAndProcess`, scenario S10) in `bucket_replication.py`.

  PRD 8.7 forbids a ticket editing another ticket's modules, so T3 doesn't re-implement or re-test them. Their acceptance criteria from WMCP-4 (bare queries, confirm and read-only refusal, XML error mapping, the backfill job ID as JSON) are covered by T1's tests: `test_bucket_delete_force_*`, `test_bucket_rename_*`, `test_bucket_inventory_*` and `test_bucket_replication_backfill_*`.
- Object Lock / retention (`object_lock.py`, T1) and ordinary object reads and writes (T2).
- Live tests (ticket 24 / T7), `tests/catalogue.py` and `docs/tools.md`.
- No edits to shared files: `config.py`, `clients.py`, `safety.py`, `errors.py`, `rest.py`, `server.py`, `tools/__init__.py`, `tests/conftest.py`, `tests/helpers.py`, `pyproject.toml` and CI.

## Technical Approach
- **Pattern:** copy `tools/meta.py`. Each module exposes `register(server, ctx)` and adds tools with `@server.tool(name=..., annotations=READ|WRITE|DESTRUCTIVE)`. `discover_groups` finds them automatically.
- **Transport:** every tool calls `rest.signed_s3(ctx.settings, method, bucket, key, query={"compose": None}, headers=..., body=..., region=..., action="<Action>")`. Bare subresources are `None` values. Responses are parsed with `rest.parse_xml`, which strips namespaces.
- **Region (5.3):** for W/D tools without an explicit `region`, resolve the bucket region with `GetBucketLocation` via `ctx.clients.s3(settings.region)`. An empty or `None` LocationConstraint maps to `us-east-1`, and results are cached in a per-`register` dict. Each module keeps a private helper (8.7), as T1 does. R tools also take an optional `region`, which defaults to the settings region.
- **MOVE headers:**
  - `Destination` is the destination key, either as a path `/<bucket>/<dest_key>` or as the full URL on the same endpoint, following Wasabi's docs (see Q1).
  - `Overwrite: true|false`.
  - `X-Wasabi-Prefix: true` for the prefix variant.
- **Compose body:** Wasabi's documented XML lists source objects in order. Validate 1–32 sources, all non-empty keys, before signing.
- **Append:** UTF-8 encode, refuse more than 1 MiB, then send with `Content-Type` (default `text/plain`). Return the ETag and append position/length headers where Wasabi sends them.
- **Compliance XML:** build `<BucketComplianceConfiguration>` / `<ObjectComplianceConfiguration>` elements with `xml.etree.ElementTree`. Validate `lock_time` as an ISO 8601 date/time, `now` or `off`. Validate `retention_days` as a non-negative integer.
- **Tests:** respx routes on `FAKE['s3']` via `rest_server`. Each test asserts:
  - `route.calls.last.request.method` (including `"MOVE"`);
  - the exact URL string (path-style, bare query);
  - `Authorization` starting with `AWS4-HMAC-SHA256` and `X-Amz-Content-SHA256` present;
  - headers and body as relevant.

  Further tests cover read-only refusal (`read_only_server`, with no route called), `confirm=false` refusal, validation refusals, and an XML `<Error>` mapped to `ToolRefused` with Code and Message. Region lookup in tests is mocked with a respx `GET /<bucket>?location` route, or avoided by passing `region` explicitly.

---

## Questions
<!-- Answer each question before running /dmx/plan. -->

1. What form should the MOVE `Destination` header take: a path (`/bucket/newkey`) or a full URL? Wasabi's docs show the full URL form for object rename.
   Answer: Follow Wasabi's documented form: the full URL of the destination on the same regional endpoint (`https://s3.<region>.wasabisys.com/<bucket>/<dest_key>`), with the key percent-encoded like the request path. T1's `bucket_rename` should be checked for consistency when planning.

2. Should `object_rename_prefix` with `overwrite=false` refuse up front, or pass the header and report Wasabi's per-key failures?
   Answer: Pass `Overwrite: false` and return Wasabi's per-key results (successes and errors) parsed from XML. Don't refuse locally, because the prefix may contain thousands of keys.

3. How does `object_compliance_set` know the current retention, so it can refuse a shorter one?
   Answer: When `retention_time` is given, GET `?compliance` first and compare with the returned RetentionTime. Refuse before the PUT if the new value is earlier. The GET happens after the guards, so a refused call still sends nothing.

4. Should region lookup happen for the R tools too?
   Answer: No. Per PRD 5.3, a wrong-region endpoint allows GET, so R tools use `region` or the settings region. W/D tools resolve the bucket's region when it isn't given.

5. Are T1's tests for the bucket-level extensions (force-delete, rename, inventory, backfill) enough to meet WMCP-4's criteria for them?
   Answer: Yes. They cover the exact URLs, MOVE, `force_delete=true`, `manifestCreateAndProcess`, confirm and read-only refusal, and XML errors. Duplicating them here would break PRD 8.7. If T1 changes before merge, the reviewer of T1 owns those criteria.

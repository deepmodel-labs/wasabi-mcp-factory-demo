---
ticket: gh-15
branch: feature-gh-15-add-catalogue-contract-tests-multipart
summary: Add catalogue contract tests, multipart tools, live suite and README docs
ticketing: github-issues
---

# Add catalogue contract tests, multipart tools, live suite and README docs

**Ticket:** [#15](https://github.com/deepmodel-labs/wasabi-mcp-factory-demo/issues/15)
**Type:** feature
**Branch:** feature-gh-15-add-catalogue-contract-tests-multipart

Item: WMCP-8 (T7 of 7 in WMCP-B2). PRD ticket 24.

---

## Context
Tool groups 1–23 are on `main`, but nothing checks the server against the PRD section 6 catalogue, there's no live suite and the README doesn't list the tools. On `main`, 160 of the 165 section 6 tools are registered (plus `server_info`, 166 in all per PRD 6.15). The 5 multipart tools from PRD 6.5 are missing. Per the architect's note on the item, this ticket implements them, exactly as PRD 6.5 and section 5 specify, so the contract test passes against the full catalogue without being weakened.

## Scope
- **Multipart tools** in `src/wasabi_mcp/tools/transfer.py` (PRD 6.5):
  - `multipart_upload_list` (R, ListMultipartUploads): includes initiation time; the description notes that Wasabi deletes incomplete uploads after about 31 days. List envelope `{items, next_token, truncated}` with `max_items`/`continuation_token`.
  - `multipart_part_list` (R, ListParts): list envelope.
  - `multipart_upload_abort` (D, AbortMultipartUpload): `confirm: bool = False`, `require_confirmed` first.
  - `multipart_upload_presign` (W, CreateMultipartUpload plus presigned UploadPart): `part_count` 1–10,000, `expires_seconds` within the existing limits; returns `upload_id` and one URL per part.
  - `multipart_upload_complete` (W, CompleteMultipartUpload): takes `parts: [{part_number, etag}]`.
  - Unit tests in `tests/tools/test_transfer.py` (happy path, read-only refusal, confirm refusal, error path), and rows in `docs/tools/transfer.md` in catalogue order. Update the module docstring and the doc's "added by another item" line.
- **`tests/catalogue.py`**: every section 6 tool plus `server_info`, with kind (R/W/D) and module.
- **`tests/test_catalogue.py`**:
  - The registered tool set equals the catalogue (missing and extra tools both fail, each named in the message). The total is 166.
  - Annotations match the kind (READ/WRITE/DESTRUCTIVE), and each tool comes from its catalogue module.
  - Every W and D tool refuses on the default read-only server with no network call. Every D tool has a `confirm` parameter defaulting to false and refuses without `confirm=true` on a writable server.
  - Every R tool, called with `conftest.SECRETS` in settings, never returns those strings (PRD 8.5). Error text counts too.
- **`tests/live/`** (`@pytest.mark.live`, skipped unless `WASABI_LIVE=1` and keys are set): S1, S2, S4, S5, and the PRD 8.4 flows: bucket create, versioning, tagging and delete; object put, get, copy, rename and delete; presigned GET (fetched over HTTP); lifecycle set, get and delete; IAM user, group, policy, attach and delete; Stats summary. Names use `wasabi-mcp-test-<uuid>`, and fixtures clean up in `finally`/teardown even when a test fails.
- **`docs/tools.md`**: assembled from `docs/tools/*.md`, grouped by area, plus a unit test that its tool names equal `list_tools`.
- **README**: tools by area (or a link to `docs/tools.md` plus a per-area list), every PRD section 7 variable, and how to run lint, unit and live tests.

## Out of Scope
- Live tests for WACM Connect: they need a partner control account. S6/S7 stay covered by unit tests.
- Live tests for S3 (contractor onboarding), S8 to S10: not required by the ticket or PRD 8.4. S9 would create a compliance-locked bucket that can't be deleted.
- Changing `.github/workflows/ci.yml`: the runner can't write `.github/`. CI already runs `uv run pytest`, which deselects live tests through `addopts`.
- Consolidating the duplicated per-module bucket region helpers.

## Technical Approach
- Multipart tools follow `transfer.py`'s existing patterns. They use `objects_read._s3(ctx, bucket, region)` and `_bucket_region` for the bucket's region client, `_check_expires`/`_presign` for URLs, `_check_max_items`, and `clean()` with `explain(exc, action)`. Guards come before any call. They're tested with moto (it supports multipart uploads), and the error paths use NoSuchUpload / NoSuchBucket.
- The catalogue test uses `server.list_tools()` and maps each tool to its module through the group discovery in `wasabi_mcp.tools` (or the registration call per module, by registering each module on a fresh `MCPServer`). Refusal tests build arguments from each tool's input schema: required params get type-appropriate dummies (`bucket="b"`, dicts `{}`, lists `[]`), and `confirm=False`. They run on servers whose endpoints point at an unroutable or respx-guarded host, and `respx` with `assert_all_called=False` plus a no-route policy proves no HTTP request. For boto3 calls, patch `Clients._client` (or the botocore `before-send` event) to fail the test if anything is sent.
- The secret-leak test calls every R tool on `rest_settings`/moto-backed settings with fake secrets. Errors are allowed but their text is checked too.
- Live tests reuse `tests.helpers.call` on a server built from `Settings.from_env()` with read-only off. A session-level finalizer removes any `wasabi-mcp-test-` bucket, user, group or policy created by this run.

---

## Questions

1. How does the test know which module registered each tool, given that `discover_groups` registers all modules on one server?
   Answer: Register each group module on its own fresh `MCPServer` (`module.register(server, ctx)`) and record the names it adds. That checks the PRD's module column without editing shared source.

2. Should the catalogue include `server_info`, so the total is 166 per PRD 6.15?
   Answer: Yes. PRD 8.1 says `list_tools` returns exactly 166, and `server_info` is the `meta` group.

3. How do the refusal tests guarantee no network call for every W/D tool, including the REST (httpx) and boto3 paths?
   Answer: Use respx in strict mode (any request raises), and register a botocore `before-send` hook on all clients via a patched `Clients._client` that raises. WACM tools run with WACM configured (`rest_settings`) so they reach their guard rather than the "not configured" refusal. Either refusal is acceptable only if its message is the safety guard's.

4. Where should the S2 live test get `.mp4` objects?
   Answer: It uploads two small `.mp4` keys and one `.txt` under `media/` in its own test bucket, then checks that `presign_get_for_prefix` returns only the `.mp4` URLs and that each URL downloads.

5. Does the live Stats summary need to tolerate a new account with no records yet?
   Answer: Yes. Assert the result shape and that no error is raised, not that records exist. The Stats API lags about a day, so new test buckets won't show up in S1.

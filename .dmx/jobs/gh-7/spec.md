---
ticket: gh-7
branch: feature-gh-7-add-s3-object-and-presigned-url-tool
summary: Add S3 object and presigned-URL tool groups
ticketing: github-issues
---

# Add S3 object and presigned-URL tool groups

**Ticket:** [#7](https://github.com/deepmodel-labs/wasabi-mcp-factory-demo/issues/7)
**Type:** feature
**Branch:** feature-gh-7-add-s3-object-and-presigned-url-tool

---

## Context
The foundation (PRD section 4, #1) is merged, but no object tools exist, so an agent can't read, write or share objects in a Wasabi bucket. This is T2 of 7 in #3 (item WMCP-3). It adds the object tools from PRD section 6 as new tool-group modules, found automatically by `discover_groups()`. Object data never passes through the model beyond the text cap; large transfers go through presigned URLs.

## Scope
- `src/wasabi_mcp/tools/objects_read.py` (PRD 6.2, 6 tools): `object_list` (R), `object_version_list` (R), `object_get_info` (R), `object_get_text` (R), `object_tagging_get` (R), `object_search` (R).
- `src/wasabi_mcp/tools/objects_write.py` (PRD 6.3, 7 tools): `object_put_text` (W), `object_copy` (W), `object_delete` (D), `object_delete_many` (D), `object_tagging_set` (W), `object_tagging_delete` (D), `object_metadata_update` (W).
- `src/wasabi_mcp/tools/object_lock.py`, object-level tools from PRD 6.8: `object_retention_get` (R), `object_retention_set` (D), `object_legal_hold_get` (R), `object_legal_hold_set` (D).
- `src/wasabi_mcp/tools/transfer.py`, presign tools from PRD 6.5: `presign_get` (R), `presign_put` (W), `presign_get_for_prefix` (R).
- Tests: `tests/tools/test_objects_read.py`, `test_objects_write.py`, `test_object_lock.py`, `test_transfer.py` (moto; respx only where moto lacks support).
- Docs: `docs/tools/objects_read.md`, `objects_write.md`, `object_lock.md`, `transfer.md` (name, kind, description table in catalogue order).

## Out of Scope
- Multipart tools in `transfer.py` (`multipart_upload_list`, `multipart_part_list`, `multipart_upload_abort`, `multipart_upload_presign`, `multipart_upload_complete`) — not named in this item.
- Bucket Object Lock configuration (`bucket_object_lock_get` / `_set`) — bucket-level, not object.
- Wasabi object extensions (`objects_ext.py`: MOVE, compose, append) — not named in this item.
- Bucket tools (`buckets.py`), live tests (`tests/live/`, PRD ticket 24), `docs/tools.md` assembly.
- Any edit to shared files: `server.py`, `tools/__init__.py`, `tests/conftest.py`, `tests/helpers.py`, `pyproject.toml`, `.github/`.

## Technical Approach
- Each module copies `tools/meta.py`: `register(server, ctx)` with `@server.tool(name=..., annotations=READ|WRITE|DESTRUCTIVE)`; docstring is the MCP description.
- Guards first: W tools call `require_writable(ctx.settings, "<Action>")`, D tools take `confirm: bool = False` and call `require_confirmed(...)` before any client call.
- Region: optional `region`; when absent, resolve the bucket's region with `GetBucketLocation` (cached per bucket within the module, since `buckets.py` isn't shared and shared files can't be edited) and use `ctx.clients.s3(region)`, which uses `s3_endpoint_for(region)`. Presigned URLs are generated from that regional client, so their host is `s3_endpoint_for(region)`.
- Outputs via `clean()`; lists return `{"items", "next_token", "truncated"}` with `max_items` (default 100, at most 1,000) and `continuation_token`; sizes include `size_human`.
- `object_get_text`: ranged GetObject of `max_bytes + 1` (default 256 KiB, cap 1 MiB); if larger, return the first `max_bytes` with `truncated: true` and a note; refuse non-UTF-8/binary content with a hint to use `presign_get`.
- `object_put_text`: reject bodies over 1 MiB (encoded size) before any call; `overwrite=False` → HeadObject first and refuse if the key exists. `object_copy` same overwrite rule.
- `object_delete_many`: `keys` (≤1,000) or `prefix` + `max_keys` (≤10,000), batched DeleteObjects of 1,000; returns `deleted` and `errors`.
- `presign_get_for_prefix`: paginate ListObjectsV2 under `prefix`, filter by `suffix`, presign each match until `max_items`; return `items` of `{key, size, url}` plus `next_token`/`truncated`. `expires_seconds` 60–604,800, default 3,600, validated before any call.
- Errors: catch `ClientError`/`BotoCoreError` → `raise explain(exc, "<Action>")`; missing tagging returns `{"configured": false}`.
- Tests use the `server`, `read_only_server`, `settings` fixtures and `tests.helpers.call`; S2 test puts >1 page of objects (use a small page size on the listing, e.g. internal `MaxKeys`, so moto pages) with mixed suffixes.

---

## Questions
<!-- Answer each question before running /dmx/plan. -->

1. Where do the four object retention/legal-hold tools live, given PRD 9 assigns all of `object_lock.py` (including `bucket_object_lock_get/_set`) to one ticket?
   Answer: Put them in `object_lock.py` per the catalogue; this item adds only the four object-level tools. Another item can add the two bucket-level tools to the same module later.

2. The item names only presigned GET/PUT; does `transfer.py` here include the multipart tools?
   Answer: No. Only `presign_get`, `presign_put`, `presign_get_for_prefix`. Multipart stays out of scope.

3. How should the bucket-region lookup be shared across the four new modules without editing shared files?
   Answer: Each module may import a small helper from one of the new modules (e.g. `objects_read._bucket_region`) or keep a private one; no change to `clients.py` or `tools/__init__.py`.

4. moto's support for object retention and legal hold needs a bucket created with Object Lock enabled; does moto honour `x-amz-bypass-governance-retention`?
   Answer: Test the happy path and guards with moto on an Object-Lock bucket; if moto doesn't enforce governance bypass, assert the header is sent (botocore event hook) rather than its effect.

5. For `object_get_text`, refuse or truncate above `max_bytes`?
   Answer: Truncate to `max_bytes` with `truncated: true`, `size` and a note pointing to `presign_get`; refuse outright only for binary content or `max_bytes` above 1 MiB.

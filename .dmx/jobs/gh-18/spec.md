---
ticket: gh-18
branch: bug-gh-18-fix-objectgettext-crash-on-appended
summary: Fix object_get_text crash on appended objects by disabling flexible checksums
ticketing: github-issues
---

# Fix object_get_text crash on appended objects by disabling flexible checksums

**Ticket:** [#18](https://github.com/deepmodel-labs/wasabi-mcp-factory-demo/issues/18)
**Type:** bug
**Branch:** bug-gh-18-fix-objectgettext-crash-on-appended

---

## Context
WMCP-10, T2 of 5 in WMCP-B3 (bugs found by live testing on Wasabi). On real Wasabi, `object_get_text` on an object that was appended to with `object_append` fails with an unhandled `AttributeError: 'StreamingChecksumBody' object has no attribute 'strip'`. It should return the original text followed by the appended text. Wasabi doesn't support AWS flexible checksums, and the pinned botocore (1.44.7) validates response checksums by default, so the response body gets wrapped in `StreamingChecksumBody`. The moto stand-in never shows this, so the existing tests pass.

## Scope
- `src/wasabi_mcp/clients.py`: one shared base botocore `Config` (retries standard, `response_checksum_validation="when_required"`, `request_checksum_calculation="when_required"`), merged into the S3 config (s3v4, path style) and used for IAM and STS, so every client built through `Clients._client` gets both options.
- `src/wasabi_mcp/tools/objects_read.py` `object_get_text`: any botocore failure, including one raised while reading the streaming body (`BotoCoreError`, plus the checksum `FlexibleChecksumError`, which is a `BotoCoreError`), goes through `explain(exc, "GetObject")` as a `ToolRefused`. Unexpected non-botocore errors from the body read (such as the `AttributeError` above) also become a clear `ToolRefused` naming GetObject, so nothing unhandled reaches the client.
- `tests/test_clients.py`: a test that `s3()` (default and another region), `iam()` and `sts()` clients all have `client.meta.config.response_checksum_validation == "when_required"` and `request_checksum_calculation == "when_required"`.
- Regression test (in the objects test module, e.g. `tests/tools/test_objects_read.py` or a new `tests/tools/test_objects_append_read.py`): `object_put_text`, `object_append` (respx on the moto host for `signed_s3`), then `object_get_text` on one key, with GetObject's response replaced by a botocore hook copying Wasabi's appended-object response, headers included (e.g. `x-amz-checksum-crc32` / `x-amz-checksum-type: COMPOSITE`-style headers that don't match the full body, `Content-Range`, `ETag` with a part suffix). Asserts the text is original + appended and no exception.
- A test that a botocore error (e.g. a hook raising `FlexibleChecksumError` / a `BotoCoreError` on the body read) gives a tool error, not an unhandled exception.
- Live test in `tests/live/test_buckets_objects.py` (marked `live`, skipped unless `WASABI_LIVE=1` by the existing live conftest): put_text, append, get_text on a `new_name` resource.

## Out of Scope
- `object_append` failing on a key that doesn't exist yet, `bucket_tagging_get` after delete, `iam_policy_validate`, and REST-path network error messages: other tickets in WMCP-B3.
- Changing `object_append` itself or the `rest.py` httpx path (it doesn't use botocore checksums).
- Retries or a fallback that re-reads without checksums; the Config change removes the cause.

## Technical Approach
- Bug-fix ticket, so editing the shared `clients.py` is allowed. Keep `Clients._client` as the single place clients are built; define e.g. `BASE_CONFIG = Config(retries=..., response_checksum_validation="when_required", request_checksum_calculation="when_required")` and build S3's config with `BASE_CONFIG.merge(Config(signature_version="s3v4", s3={"addressing_style": "path"}))`.
- Check that existing tests that patch `Clients._client` or build their own `Clients` still pass.
- In `object_get_text`, keep the existing `ClientError`/`InvalidRange` handling; widen the body-read handling so the read is inside the `try` (it already is) and add a final catch for unexpected exceptions from the stream that raises `ToolRefused("GetObject failed while reading the object body: ...")` without leaking credentials.
- Mock Wasabi's response with a botocore `before-send` hook returning an `AWSResponse` with the recorded headers and body (respx can't intercept boto3), as described in techContext.
- Add a techContext note: Wasabi doesn't support flexible checksums; clients use `when_required`.

---

## Questions
<!-- Answer each question before running /dmx/plan. -->

1. What exact headers does Wasabi return on GetObject for an appended object, to copy into the mock?
   Answer: Not recorded in the item. Use a plausible set that triggers botocore's validation path today (a `x-amz-checksum-crc32` header whose value doesn't match the full body, plus `Content-Range`, `ETag`, `Content-Type`, `Content-Length`, `Last-Modified`, `x-amz-request-id`); the test must fail on `main`'s config and pass with the fix. The live test confirms the real behaviour.

2. Should the shared config also apply to IAM and STS, which never stream bodies?
   Answer: Yes. The item asks that every Wasabi client is covered, and the acceptance criterion checks every client from `clients.py`.

3. Should a non-botocore exception from the body read (like the original `AttributeError`) be caught too?
   Answer: Yes, scoped to the GetObject call and body read only, and turned into a `ToolRefused` naming GetObject, so no unhandled exception reaches the client if a similar botocore bug recurs.

4. Where does the live test go?
   Answer: `tests/live/test_buckets_objects.py`, reusing its bucket fixture and `new_name`, so cleanup is handled by the session finalizer.

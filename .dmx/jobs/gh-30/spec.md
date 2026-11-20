---
ticket: gh-30
branch: bug-gh-30-create-the-object-when-objectappend
summary: Create the object when object_append targets a missing key on Wasabi
ticketing: github-issues
---

# Create the object when object_append targets a missing key on Wasabi

**Ticket:** [#30](https://github.com/deepmodel-labs/wasabi-mcp-factory-demo/issues/30)
**Type:** bug
**Branch:** bug-gh-30-create-the-object-when-objectappend

---

## Context
WMCP-11, T3 of 5 in WMCP-B3 (bugs found by live testing on Wasabi). **Observed:** on real Wasabi, `object_append` to a key that doesn't exist yet fails, because Wasabi's `PUT /{bucket}/{key}?append` answers `NoSuchKey`. **Expected:** the tool creates the object with the given text. The respx tests pass because they never model Wasabi's NoSuchKey reply to an append.

## Scope
- `src/wasabi_mcp/tools/objects_ext.py` `object_append`:
  - When the `?append` PUT fails with `NoSuchKey` (and only then), PUT the same body and `Content-Type` to the key as a new object (`rest.signed_s3` PUT without the `append` query, action `PutObject`) and return `created: true`.
  - A normal append to an existing key returns `created: false`, so the result always has the same shape (`ok`, `bucket`, `key`, `appended_bytes`, `etag`, `version_id`, `region`, `created`, plus any `x-wasabi-*` headers).
  - Every other append error (e.g. `AccessDenied`, `TooManyParts`) is reported exactly as today (`AppendObject failed: <Code>: <Message>`), with no PUT.
  - If the fallback PUT fails, report it as `PutObject failed: …` so the client sees which step failed.
  - Docstring: says that a missing key is created, plus one line that Wasabi has no conditional create here, so a concurrent writer could race with the fallback PUT.
- `src/wasabi_mcp/rest.py`: let callers tell NoSuchKey apart without matching the message text, e.g. add an optional `code` attribute to the `ToolRefused` raised by `signed_s3` (shared helper; keep the message unchanged).
- Tests in `tests/tools/test_objects_ext.py` (respx on `rest_server`):
  - Append mocked to return Wasabi's real NoSuchKey XML (`404`, `<Error><Code>NoSuchKey</Code><Message>The specified key does not exist.</Message><Key>…</Key>…</Error>`); assert a second PUT without `?append` carries the text and content type, and `created: true`.
  - Existing key: `created: false` and only one request.
  - AccessDenied (403): a clear tool error, and the PUT route is never called.
  - Description mentions creating a missing key and the race.
- A moto-backed round trip (append to a missing key → read back with `object_get_text` returns the text; then append again → original + new text, `created: false`). moto doesn't implement `?append`, so a respx override for the moto host returns NoSuchKey on the append and passes the plain PUT through, or follows the pattern in `tests/tools/test_objects_read.py` around line 249.
- Live test in `tests/live/test_buckets_objects.py`: append to a new fixed key in the `new_name` bucket, check `created: true`, and read back the text. It is skipped unless `WASABI_LIVE=1` (the existing live conftest already handles this).
- `docs/tools/objects_ext.md` updated; `docs/tools.md` regenerated with `uv run python -m tests.docs_tools`.

## Out of Scope
- Any conditional or atomic create (Wasabi has none here); the race is accepted by the architect.
- Retrying the append after the PUT, or any locking.
- Changes to other object tools (`object_put_text`, `object_compose`, etc.) or to `object_get_text` (gh-18 fixed that).
- The REST-path network-failure messages (a separate ticket in WMCP-B3).

## Technical Approach
- Follows the existing `rest.signed_s3` pattern in `objects_ext.py`: guards (`require_writable`, 1 MiB check) run first, then `bucket_region`, then the signed call.
- Wrap only the append `signed_s3` call in `try/except ToolRefused` and check the error code. Don't wrap `bucket_region`, which raises its own `ToolRefused` (techContext: a catch-all must not wrap helpers that raise their own clear errors).
- Fallback: `rest.signed_s3(settings, "PUT", bucket, key, headers={"Content-Type": content_type}, body=body, region=name, action="PutObject")`. Take `etag`/`version_id` from that response.
- Tool error text in tests carries the SDK prefix, so use `match`/`endswith`, not `^`-anchored patterns.
- respx matches routes in registration order: register the `?append` route (params match) before the plain PUT route.

---

## Questions
<!-- Answer each question before running /dmx/plan. -->

1. How should `object_append` recognise NoSuchKey: by a `code` attribute on `ToolRefused` from `rest.signed_s3`, or by matching the message text?
   Answer: Add an optional `code` to `ToolRefused` (default `None`), set by `signed_s3` from the parsed `<Error><Code>`. It's backward compatible and avoids brittle string matching. `iam_query` can stay as it is.

2. Should `appended_bytes` still be returned when the object was created rather than appended to?
   Answer: Yes. Keep the same shape; `appended_bytes` is the number of bytes written, and `created` says which path ran.

3. If the fallback PUT itself fails (e.g. AccessDenied on PutObject), what does the client see?
   Answer: The PUT's own error, `PutObject failed: <Code>: <Message>`, unchanged from `signed_s3`.

4. Does Wasabi return NoSuchKey as HTTP 404 with the standard S3 `<Error>` body for `?append`?
   Answer: Assumed yes (standard S3 error XML, which `_xml_error` already parses). The live test confirms it; detection uses the code, not the status.

5. Should the moto round-trip test go through respx on the moto host or through a botocore hook?
   Answer: respx on the moto host. `object_append` uses httpx (`rest.signed_s3`), which respx intercepts; use `side_effect` to return NoSuchKey for `?append` and pass the plain PUT through to moto (`respx.route(...).pass_through()`), so `object_get_text` (boto3) reads the real object.

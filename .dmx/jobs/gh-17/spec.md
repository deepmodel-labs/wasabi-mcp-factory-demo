---
ticket: gh-17
branch: bug-gh-17-report-unconfigured-bucket-tagging-when
summary: Report unconfigured bucket tagging when Wasabi returns an empty tag set
ticketing: github-issues
---

# Report unconfigured bucket tagging when Wasabi returns an empty tag set

**Ticket:** [#17](https://github.com/deepmodel-labs/wasabi-mcp-factory-demo/issues/17)
**Type:** bug
**Branch:** bug-gh-17-report-unconfigured-bucket-tagging-when

---

## Context
Live testing on Wasabi (us-east-1, 2026-11-20) found that after `bucket_tagging_delete`, Wasabi's
GetBucketTagging returns `200` with an empty `<TagSet/>`, where AWS (and moto) return the
`NoSuchTagSet` error. `bucket_tagging_get` only maps `NoSuchTagSet` to unconfigured, so on Wasabi it
reports `{"configured": true, "bucket": ..., "tags": {}}`.

- **Observed:** `{"configured": true, "bucket": "<name>", "tags": {}}` after the tags are deleted.
- **Expected (PRD 5.5):** `{"configured": false}` when no tags are set.

This is T1 of 5 in WMCP-B3 (issue #17). T2–T5 (flexible checksums, append to a missing key,
iam_policy_validate encoding, REST transport errors) are separate items.

## Scope
- `src/wasabi_mcp/tools/buckets.py` → `bucket_tagging_get`: return `{"configured": false}` when the
  response's `TagSet` is missing or empty, as well as on the `NoSuchTagSet` error.
- Keep `{"configured": true, "bucket": ..., "tags": {...}}` when at least one tag exists.
- `tests/tools/test_buckets.py`: a regression test where GetBucketTagging answers the way Wasabi
  does (an empty TagSet) and the tool returns `{"configured": false}`. The existing moto tests
  already cover `NoSuchTagSet` and the tagged case; keep them passing.
- `tests/live/test_buckets_objects.py::test_bucket_create_versioning_tagging_delete` already asserts
  `{"configured": False}` after delete against real Wasabi. Check that it stays `live`-marked and
  skips unless `WASABI_LIVE=1` (the `live_settings` fixture in `tests/live/conftest.py` does this).

## Out of Scope
- Object tagging (`object_tagging_get`). The live test didn't report it and it isn't in T1.
- Bugs 2–5 from issue #17 (T2–T5).
- Running the live suite. The factory and CI have no Wasabi credentials, so the architect runs it by
  hand.

## Technical Approach
- Tool layer only (`tools/buckets.py`). After a successful `get_bucket_tagging`, build `tags` from
  `response.get("TagSet") or []`. If it is empty, return `{"configured": False}`, the same shape the
  `NoSuchTagSet` branch already returns. Error handling stays as it is (`explain(exc, "GetBucketTagging")`).
- Update the docstring only if it needs to say that an empty tag set counts as none. The current text
  ("or {"configured": false} when it has none") already covers this.
- Regression test: botocore talks to moto over urllib3, so respx (which patches httpx) can't fake S3
  responses. Use a moto override: wrap the server's S3 client so `get_bucket_tagging` returns Wasabi's
  shape, `{"TagSet": [], "ResponseMetadata": {...}}`. Either attach a botocore `Stubber` to
  `ctx.clients.s3()` (the repo already does this for IAM in `tests/tools/iam_support.py`) or
  monkeypatch the moto backend's tagging lookup to return an empty set. The plan picks one; prefer
  the Stubber because the repo already uses it.
- Run `test_command` (ruff check, ruff format --check, pytest -q). It excludes `live` tests.

---

## Questions
<!-- Answer each question below before running /dmx/plan. -->

1. Should an empty TagSet return `{"configured": false}` exactly, without the `bucket` key, the same
   as the `NoSuchTagSet` branch?
   Answer: Yes. The acceptance criteria, PRD 5.5 and the live test's exact-equality assertion all
   expect `{"configured": False}` with no other keys.

2. How do we reproduce Wasabi's response, given that respx can't intercept boto3 S3 calls?
   Answer: Use a moto override, which the ticket allows ("a respx mock or moto override"): a botocore
   `Stubber` on the server's S3 client returning `{"TagSet": []}`, following `tests/tools/iam_support.py`.
   If the server's client caching makes Stubber awkward, patch the client's `get_bucket_tagging`
   instead. The plan picks one.

3. Does the live test need changes?
   Answer: No code change is expected. It already has the module-level `pytestmark = pytest.mark.live`,
   it asserts `{"configured": False}` after delete, and `live_settings` skips unless `WASABI_LIVE=1`.
   Pytest's default `addopts = "-m 'not live'"` also deselects it. Implementation only checks this.

4. Should a response without a `TagSet` key at all also count as unconfigured?
   Answer: Yes. Treat a missing key and an empty list the same (`response.get("TagSet") or []`).

# Tasks: Add S3 bucket and bucket-configuration tools

**Spec:** `.dmx/jobs/gh-3/spec.md`
**Branch:** feature-gh-3-add-s3-bucket-and-bucket-configuration
**Ticket:** [gh-3](https://github.com/deepmodel-labs/wasabi-mcp-factory-demo/issues/3)
**Status:** In Progress

Every module follows PRD section 5 and the `tools/meta.py` pattern: `register(server, ctx)`, catalogue names and kinds, `READ`/`WRITE`/`DESTRUCTIVE` annotations, guards before any client call, `confirm: bool = False` on D tools, `clean()` output, `{"configured": false}` for absent optional config, `explain(exc, "<Action>")` on botocore errors, and a private per-module `_bucket_client` (GetBucketLocation + per-`register` cache, empty/None → `us-east-1`, explicit `region` skips lookup) for bucket writes. No shared files are edited (PRD 8.7). Each phase ends with `uv run ruff check . && uv run ruff format --check . && uv run pytest -q` passing.

---

## Phase 1: Bucket tools (`buckets.py`, 11 tools)
- [x] Add `src/wasabi_mcp/tools/buckets.py` with `_bucket_client` region helper and the read tools `bucket_list` (`include_region`, items/next_token/truncated shape), `bucket_get`, `bucket_location_get` (region + service URL), `bucket_versioning_get`, `bucket_tagging_get` (`{"configured": false}` on NoSuchTagSet)
- [x] Add write tools `bucket_create` (`region`, `object_lock`, LocationConstraint for non-us-east-1, description says lock is irreversible and enables versioning; surface TooManyBuckets), `bucket_versioning_set` (`enabled`; Suspended when false), `bucket_tagging_set` (`tags: dict[str, str]`)
- [x] Add destructive tools `bucket_tagging_delete`, `bucket_delete` (`force=True` → `rest.signed_s3` `DELETE ?force_delete=true`, else boto3 DeleteBucket) and `bucket_rename` (`rest.signed_s3` `MOVE /bucket` with `Destination` header)
- [x] Add `tests/tools/test_buckets.py`: moto happy paths, read-only refusals with state checks, `confirm=false` refusals, NoSuchBucket error-format test (`"<Action> failed: <Code>: <Message>"`), region-resolution/cache test, and respx tests on `rest_server` for `force_delete` and `MOVE`
- [x] Add `docs/tools/buckets.md` (name, kind, description table in catalogue order)

## Phase 2: Access — bucket policy and ACLs (`access.py`, 7 tools)
- [ ] Add `src/wasabi_mcp/tools/access.py` with `bucket_policy_get` (JSON doc; `{"configured": false}` on NoSuchBucketPolicy), `bucket_policy_set` (dict or JSON str, validated before calling Wasabi; note about Wasabi account/canonical IDs), `bucket_policy_delete`
- [ ] Add `bucket_acl_get`, `object_acl_get` (owner and grants, `version_id`) and `bucket_acl_set` / `object_acl_set` (`canned_acl` validated against private/public-read/public-read-write/authenticated-read, or explicit `grants`; exactly one required; description notes the public-bucket restriction for trial / post-2023-03-13 accounts)
- [ ] Add `tests/tools/test_access.py` (moto happy paths, invalid JSON / invalid canned ACL rejected before any call, read-only and `confirm=false` refusals, policy-unchanged state checks) and `docs/tools/access.md`

## Phase 3: CORS and lifecycle (`bucket_rules.py`, 6 tools)
- [ ] Add `src/wasabi_mcp/tools/bucket_rules.py` with `bucket_cors_get` (`{"configured": false}` on NoSuchCORSConfiguration; description mentions Wasabi's permissive default headers), `bucket_cors_set` (rules: allowed origins/methods/headers, expose headers, max age), `bucket_cors_delete`
- [ ] Add `bucket_lifecycle_get` (`{"configured": false}` on NoSuchLifecycleConfiguration), `bucket_lifecycle_set` (replaces all rules; rejects `Transitions`/`NoncurrentVersionTransitions` before calling Wasabi; description covers 00:00 UTC daily run, 24–48 h, locked objects, MFA Delete), `bucket_lifecycle_delete`
- [ ] Add `tests/tools/test_bucket_rules.py` (moto happy paths incl. S5 90-day `tmp/` expiry, transition rejection, refusals) and `docs/tools/bucket_rules.md`

## Phase 4: Object Lock, retention and legal hold (`object_lock.py`, 6 tools)
- [ ] Add `src/wasabi_mcp/tools/object_lock.py` with `bucket_object_lock_get` and `bucket_object_lock_set` (`mode` COMPLIANCE/GOVERNANCE, `days` xor `years`, `clear_default=true` sends `Enabled` only; description says lock can only be enabled at creation)
- [ ] Add `object_retention_get`, `object_retention_set` (`mode`, ISO 8601 `retain_until` parsed before calling, `bypass_governance`; description says compliance can't be shortened), `object_legal_hold_get`, `object_legal_hold_set` (`on: bool`)
- [ ] Add `tests/tools/test_object_lock.py` (S9 flow: lock-enabled bucket → COMPLIANCE 7 years → get; validation errors; refusals; retention/legal hold on an object put directly via boto3 in the test) and `docs/tools/object_lock.md`

## Phase 5: Logging and inventory (`bucket_ops.py`, 6 tools)
- [ ] Add `src/wasabi_mcp/tools/bucket_ops.py` with `bucket_logging_get` and `bucket_logging_set` (`target_bucket`, `target_prefix`, `enabled=false` clears; description says best effort, no SLA; surface LoggingBucketSameOwner)
- [ ] Add inventory tools via `rest.signed_s3` + `rest.parse_xml`: `bucket_inventory_list` (`?inventory`), `bucket_inventory_get` (`?inventory&id=`), `bucket_inventory_set` (XML body; CSV only, Daily/Weekly, Current/All, prefix, destination bucket/prefix, optional fields validated against Wasabi's list), `bucket_inventory_delete`
- [ ] Add `tests/tools/test_bucket_ops.py` (moto for logging; respx on `rest_server` for inventory incl. request-body assertions, invalid field rejection, S3 XML error, un-called routes on refusal) and `docs/tools/bucket_ops.md`

## Phase 6: Replication (`bucket_replication.py`, 4 tools)
- [ ] Add `src/wasabi_mcp/tools/bucket_replication.py` with `bucket_replication_get` (`{"configured": false}` on ReplicationConfigurationNotFoundError) and `bucket_replication_delete`
- [ ] Add `bucket_replication_set` (rules with prefix/tag filters, destination `arn:aws:s3:::name`, delete-marker replication, empty role same-account, role ARN + account + owner translation cross-account; versioning parity check on both buckets with an explanatory error) and `bucket_replication_backfill` (`rest.signed_s3` `PUT ?manifestCreateAndProcess`, returns job ID)
- [ ] Add `tests/tools/test_bucket_replication.py` (moto for get/set/delete incl. versioning-mismatch error; respx for backfill; refusals) and `docs/tools/bucket_replication.md`
- [ ] Run the full `test_command` and confirm `list_tools` now includes all 40 new tools with the right annotations

---
_Generated by /dmx/plan. Edit freely — this is your implementation contract._

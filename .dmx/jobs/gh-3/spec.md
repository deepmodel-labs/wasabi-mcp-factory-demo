---
ticket: gh-3
branch: feature-gh-3-add-s3-bucket-and-bucket-configuration
summary: Add S3 bucket and bucket-configuration tools
ticketing: github-issues
---

# Add S3 bucket and bucket-configuration tools

**Ticket:** [#3](https://github.com/deepmodel-labs/wasabi-mcp-factory-demo/issues/3)
**Type:** feature
**Branch:** feature-gh-3-add-s3-bucket-and-bucket-configuration

---

## Context
Issue #3 (WMCP-2) covers everything in `docs/prd.md` that isn't on `main` yet. The foundation (PRD section 4) was merged in #2. The TPM split that work into seven items (WMCP-B2, T1–T7). This item is **T1 only**: the S3 bucket and bucket-configuration tools from PRD section 6. They're built on `Clients.s3(region)`, `safety`, `errors.explain` and the group pattern in `tools/__init__.py` / `tools/meta.py`.

## Scope
T1 delivers whole tool-group modules, the ones that are about buckets and their configuration. Each module comes with `tests/tools/test_<module>.py` and `docs/tools/<module>.md` (PRD 5.1, 5.7, 8.7):

- `src/wasabi_mcp/tools/buckets.py`, 11 tools (6.1): `bucket_list`, `bucket_get`, `bucket_create` (with `object_lock`), `bucket_delete` (with `force`), `bucket_rename`, `bucket_versioning_get/set`, `bucket_tagging_get/set/delete`, `bucket_location_get`.
- `src/wasabi_mcp/tools/access.py`, 7 tools (6.6): `bucket_policy_get/set/delete`, `bucket_acl_get/set`, `object_acl_get/set`.
- `src/wasabi_mcp/tools/bucket_rules.py`, 6 tools (6.7): `bucket_cors_get/set/delete`, `bucket_lifecycle_get/set/delete`.
- `src/wasabi_mcp/tools/object_lock.py`, 6 tools (6.8): `bucket_object_lock_get/set`, `object_retention_get/set`, `object_legal_hold_get/set`.
- `src/wasabi_mcp/tools/bucket_ops.py`, 6 tools (6.10, logging and inventory): `bucket_logging_get/set`, `bucket_inventory_list/get/set/delete`.
- `src/wasabi_mcp/tools/bucket_replication.py`, 4 tools (6.10): `bucket_replication_get/set/delete`, `bucket_replication_backfill`.

That's 40 tools across PRD tickets 1, 6, 7, 8, 10 and 11. Each module follows PRD section 5:
- names and kinds exactly as in the catalogue;
- the `READ` / `WRITE` / `DESTRUCTIVE` annotations;
- guards before any network call;
- `confirm: bool = False` on D tools;
- `clean()` output, list pagination, and `{"configured": false}` for absent optional configuration;
- `explain(exc, "<Action>")` on botocore errors;
- bucket write tools resolving the bucket's region (5.3).

## Out of Scope
- Object read/write, presigned URL and multipart tools: `objects_read.py`, `objects_write.py`, `transfer.py`. That's T2.
- Wasabi object/compliance extension modules: `objects_ext.py` and `compliance.py`. That's T3.
- IAM/STS (T4), Stats (T5), WACM (T6).
- `tests/catalogue.py`, the catalogue and secret-leak contract tests, `tests/live/`, `docs/tools.md` and README. That's T7.
- Everything PRD 6.15 marks unsupported: encryption, website, Block Public Access/PolicyStatus, accelerate, analytics/metrics/intelligent-tiering, ownership controls, requester pays, notifications.
- No edits to shared files (`config.py`, `clients.py`, `safety.py`, `errors.py`, `rest.py`, `server.py`, `tools/__init__.py`, `tests/conftest.py`, `tests/helpers.py`, `pyproject.toml`, CI) (PRD 8.7).

## Technical Approach
- **Pattern:** copy `tools/meta.py`. Each module has `register(server, ctx)`, which defines tools with `@server.tool(name=..., annotations=...)`. Discovery is automatic (`discover_groups`), so no registry is edited.
- **Region resolution:** each module that changes a bucket needs a small private helper. It calls `GetBucketLocation` on the settings-region client, maps an empty or `None` LocationConstraint to `us-east-1`, caches the result in a per-`register` dict, and returns `ctx.clients.s3(region)`. To keep 8.7, each module defines its own copy rather than adding a shared helper. Explicit `region` skips the lookup.
- **boto3 vs Ext:**
  - These use `rest.signed_s3`:
    - `bucket_delete(force=True)`: `DELETE ?force_delete=true`;
    - `bucket_rename`: `MOVE /bucket` with a `Destination` header;
    - `bucket_inventory_*`: `?inventory` / `?inventory&id=`;
    - `bucket_replication_backfill`: `PUT ?manifestCreateAndProcess`.
  - Everything else uses boto3 via `ctx.clients.s3(region)`.
- **Validation before Wasabi:**
  - policy JSON (dict or str);
  - lifecycle rules rejecting `Transitions` / `NoncurrentVersionTransitions`;
  - object lock `days` xor `years`, mode in COMPLIANCE/GOVERNANCE;
  - canned ACL values;
  - inventory format CSV, frequency, versions and optional fields from Wasabi's list;
  - replication versioning parity between source and destination (GetBucketVersioning on both) before PutBucketReplication.
- **Tests:**
  - moto (`server`, `read_only_server`, `settings`) for every boto3-backed tool;
  - respx with `rest_server` / `FAKE["s3"]` for the Ext calls, and for any tool where moto doesn't model the call or Wasabi's behaviour;
  - per tool: a happy path, a read-only refusal, `confirm=false` refusal for D tools, and an error path where one is documented.
  - "No mutating request reaches moto" is proven by checking the resource state afterwards. For example, after a refused `bucket_create` the bucket doesn't exist, and after a refused `bucket_tagging_delete` the tags are still there. For respx-backed tools, an unmatched or un-called route proves it.
- **S9:** a test creates a bucket with `object_lock=true`, then calls `bucket_object_lock_set(mode="COMPLIANCE", years=7, confirm=True)`, then `bucket_object_lock_get`, and asserts COMPLIANCE / 7 years.
- **Error format:** a test triggers a moto `ClientError` (for example `bucket_get` on a missing bucket, or `bucket_versioning_get` on `NoSuchBucket`) and asserts the message matches `"<Action> failed: <Code>: <Message>"` with no traceback.

---

## Questions

1. The TPM's T1 ("standard S3 bucket tools … on top of `Clients.s3`") and T3 ("Wasabi S3 extension tools") overlap on bucket tools that call Wasabi extensions (`bucket_rename`, `bucket_delete force`, `bucket_inventory_*`, `bucket_replication_backfill`). Which item owns them?
   Answer: T1. PRD 5.1/8.7 require one module per group, and each ticket delivers whole modules. Splitting `buckets.py`, `bucket_ops.py` or `bucket_replication.py` across two items would break that and cause conflicts. T3 keeps the extension-only modules, `objects_ext.py` and `compliance.py`. The Ext calls here go through `rest.signed_s3` and are tested with respx.

2. `access.py` and `object_lock.py` include per-object tools (`object_acl_*`, `object_retention_*`, `object_legal_hold_*`). Do they belong to T1 or to T2 (objects)?
   Answer: T1, for the same whole-module reason. Both modules are about access and retention configuration, not object data. T2 covers `objects_read.py`, `objects_write.py` and `transfer.py`.

3. Where does the region-resolution helper (GetBucketLocation + cache) live, given that 8.7 forbids shared-file edits?
   Answer: a private helper in each module that needs it (`_bucket_client`), with a module-level docstring note. T7 or a later refactor can consolidate it. No edits to `clients.py`.

4. How do the tests prove "moto receives no mutating request" for refused writes?
   Answer: by state checks after the refusal. The bucket or configuration is unchanged, or doesn't exist. For respx routes, the route's `called` is false. The refusal comes from the guard, which runs before any client is created or used.

5. What should moto-backed tests do where moto lacks or diverges from Wasabi behaviour (for example `bucket_logging_set` target-owner rules, or replication versioning messages)?
   Answer: assert our own pre-checks and message shapes against moto where it works, and fall back to respx on `rest_server` for calls moto doesn't model (PRD 5.6: never skip a tool).

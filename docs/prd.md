# PRD: an MCP server for Wasabi cloud storage

| | |
|---|---|
| **Status** | Approved for build, 2026-11-19 |
| **Starting point** | This PRD, README, licence and dmx setup only. No code. |
| **Owner** | DeepModel |
| **Repository** | deepmodel-labs/wasabi-mcp-factory-demo |
| **Sources** | Wasabi's public documentation only (see [References](#12-references)) |

## 1. Summary

Build an open-source MCP (Model Context Protocol) server for Wasabi cloud storage. It gives AI agents about 160 tools across four areas:

- **Storage:** S3 and Wasabi's own S3 extensions.
- **Usage:** the Stats API.
- **Identity:** IAM and STS.
- **Account governance:** WACM Connect.

It's safe by default: read-only until enabled, explicit confirmation for anything destructive, and credentials only from the environment. It runs locally over stdio, or over streamable HTTP.

This is an independent implementation built from public documentation. It is not Wasabi's official MCP server and uses none of its code. Wasabi's official server, in beta, covers the same four service families with "140+" / "150" tools. This PRD defines its own catalogue against the same public API surface.

## 2. Goals and non-goals

### Goals

1. **Cover Wasabi's public API surface.** Implement the catalogue in [section 6](#6-tool-catalogue): every documented S3 operation Wasabi supports and that is useful to an agent, Wasabi's S3 extensions, the IAM and STS actions Wasabi lists, the Stats API, and the WACM Connect API.
2. **Be safe by default.**
   - The server starts read-only.
   - Destructive operations need `confirm=true`.
   - No tool returns configured credentials.
   - New secrets are returned once, and only on confirmation.
3. **Return results an agent can use:** plain JSON, stable field names, pagination, and clear errors that say what went wrong and how to fix it.
4. **Test every tool:**
   - unit tests against local stand-ins (moto for S3, IAM and STS; respx for Wasabi's extensions, Stats and WACM);
   - live tests against a real Wasabi account for the core flows.
5. **Keep it easy to run:** `uv run wasabi-mcp`, configured only by environment variables.

### Non-goals

- A hosted, multi-tenant service, OAuth for MCP clients, or a web dashboard.
- Moving object data through the model. Large reads and writes use presigned URLs; the text tools are capped.
- Legacy and undocumented APIs:
  - the WACA / Partner API (closed to new accounts);
  - the Console API (published only as screenshots);
  - the Wasabi AiR API (its docs have been removed);
  - event notifications (Console only, through AWS SNS).
- S3 features Wasabi documents as unsupported, such as SSE-KMS, Select, website hosting, transfer acceleration, Block Public Access and intelligent tiering. Section 6.15 lists them.
- Bulk data transfer, sync, or a CLI beyond starting the server.

## 3. Users and scenarios

**Users:** developers, operators and MSP admins who want an AI agent to work with their Wasabi account, and the Wasabi team evaluating this build.

Scenarios the catalogue must support end to end:

| # | Scenario | Tools involved |
|---|---|---|
| S1 | "List my buckets and how much each one stores." | `bucket_list`, `stats_bucket_utilization_list` |
| S2 | "Give me presigned links for every `.mp4` under `media/` in bucket X." | `presign_get_for_prefix` |
| S3 | "Onboard a read-only contractor to bucket X." | `iam_policy_create`, `iam_group_create`, `iam_user_create`, `iam_group_add_user`, `iam_policy_attach`, `iam_access_key_create` |
| S4 | "Which buckets have no bucket policy, and which policies grant write on bucket X?" | `bucket_list`, `bucket_policy_get`, `iam_policy_list`, `iam_policy_get` |
| S5 | "Apply a 90-day expiry to everything under `tmp/` in the dev buckets." | `bucket_tagging_get`, `bucket_lifecycle_set` |
| S6 | "Make a new tenant: a sub-account with a 10 TB hard quota." | `wacm_sub_account_create` |
| S7 | "Which sub-accounts are near quota this month?" | `wacm_sub_account_list`, `wacm_usage_list` |
| S8 | "Remove user Y's access everywhere." | `iam_user_get`, `iam_group_remove_user`, `iam_policy_detach`, `iam_access_key_update` / `iam_access_key_delete` |
| S9 | "Lock this bucket's objects for 7 years in compliance mode." | `bucket_create` (with `object_lock=true`), `bucket_object_lock_set` |
| S10 | "Replicate bucket A to bucket B in another region, including existing objects." | `bucket_versioning_set`, `bucket_replication_set`, `bucket_replication_backfill` |

## 4. Foundation (stage 1, ticket 0)

The repository starts with only this PRD, the README, the licence and the dmx setup. There's no code yet. Ticket 0 builds the shared foundation below. Every later ticket builds on it and must not restructure it.

### 4.1 Project

- **Tooling:** Python 3.12+, managed with uv. Commit `pyproject.toml` and `uv.lock`; the package is `wasabi_mcp`, under `src/`.
- **Dependencies:** `mcp` (Python SDK 2.x; the server class is `mcp.server.mcpserver.MCPServer`), `boto3`, `httpx`.
- **Dev dependencies:** `pytest`, `pytest-asyncio` (`asyncio_mode = "auto"`), `moto` with server mode, `respx`, `ruff` (line length 100).
- **Entry points:** the console script `wasabi-mcp` runs the server. It takes `--transport stdio|streamable-http`, defaulting to stdio. `python -m wasabi_mcp` does the same.
- **pytest:** the marker `live` is registered and deselected by default (`-m 'not live'`).

### 4.2 Modules

- **`config.py`**
  - A frozen `Settings` dataclass with `Settings.from_env()` reading the variables in section 7.
  - Endpoint helpers: `s3_endpoint_for(region)` returns `https://s3.<region>.wasabisys.com`; `iam_url`, `sts_url`, `stats_url` and `wacm_url`; each override replaces the default.
  - `has_keys` and `has_wacm`.
  - Secrets are excluded from `repr`.
- **`clients.py`:** `Clients(settings)` with cached boto3 clients:
  - `s3(region=None)`, which uses that region's endpoint;
  - `iam()` and `sts()`, signed for `us-east-1`;
  - retries in "standard" mode.
- **`safety.py`:**
  - `ToolRefused(Exception)`, whose message is what the client sees;
  - `require_writable(settings, action)`, which refuses in read-only mode and tells the user how to enable writes;
  - `require_confirmed(settings, action, confirm)`, which applies `require_writable` and then refuses unless `confirm` is true.
- **`errors.py`:** `explain(exc, action) -> ToolRefused` turns botocore `ClientError` and `BotoCoreError` into `"<action> failed: <Code>: <Message>"`.
- **`rest.py`:** requests boto3 can't make. Each raises `ToolRefused` with the service's error code and message on a non-2xx response, and with a clear message when credentials are missing.
  - `signed_s3(settings, method, bucket, key="", *, query, headers, body, region, action) -> httpx.Response`:
    - path-style and SigV4-signed for `s3`, with `x-amz-content-sha256: UNSIGNED-PAYLOAD`;
    - any method, including `MOVE`;
    - bare subresources (`?compose`, `?append`, `?compliance`, `?inventory&id=x`, `?force_delete=true`, `?manifestCreateAndProcess`).
  - `iam_query(settings, action_name, params) -> xml Element`: a SigV4-signed (`iam`, `us-east-1`) form POST to the IAM endpoint with `Version=2010-05-08`, for actions botocore doesn't model (`ValidatePolicy`, `GetUserMFA`).
  - `stats_get(settings, path, params) -> JSON`: Stats API GET with header `Authorization: <access key>:<secret key>`. Parameters set to `None` are dropped.
  - `wacm_request(settings, method, path, *, params, body) -> JSON`: WACM Connect with HTTP Basic (WACM username and API key). JSON errors `{error, code}` are reported as `HTTP <status>: <error>`.
  - `parse_xml(content)`.
- **`tools/__init__.py`:**
  - the annotations `READ`, `WRITE` and `DESTRUCTIVE` (`ToolAnnotations` with `read_only_hint`, `destructive_hint` and `open_world_hint` set);
  - `ToolContext(settings, clients)`;
  - `discover_groups()`, which returns every module in the `tools` package with `meta` first, then alphabetical;
  - `clean(value)`, which drops `ResponseMetadata`, turns datetimes into ISO 8601 and decodes bytes.
- **`tools/meta.py`:** `server_info` (R). It returns the version, region, read-only flag, endpoints, whether keys and WACM are configured, and the loaded groups. It's the reference example for tool groups and never returns secrets.
- **`server.py`:** `build_server(settings=None) -> MCPServer`. It sets instructions that describe the read-only default, then imports every module from `discover_groups()` and calls its `register(server, ctx)`. Adding a group never edits a shared list.

### 4.3 Test harness

These fixtures go in `tests/conftest.py` and are shared by every ticket:
- `moto_url`: session scope. A `ThreadedMotoServer` on 127.0.0.1 with a random port. Reset after each test that used it, through urllib, so respx never sees the reset.
- `settings` (writes allowed) and `read_only_settings`: every S3, IAM and STS endpoint points at moto, with fake keys.
- `server` and `read_only_server`: `build_server` built from those settings.
- `FAKE`: fake hosts for s3, iam, sts, stats and wacm. Also `rest_settings` (writes allowed, WACM configured) and `rest_server`, for respx-mocked tests.
- `live_settings`: skips unless `WASABI_LIVE=1`; otherwise `Settings.from_env()`.

`tests/helpers.py` provides `call(server, name, **arguments)`. It calls a tool through `server.call_tool`, returns its structured result (unwrapping `{"result": ...}`), and raises `ToolError` with the text when `is_error` is set.

`tests/tools/` is an empty package that tickets 1–23 add their tests to.

The foundation's own tests cover:
- the guards;
- the default endpoints;
- clients reaching moto;
- each `rest.py` helper against respx: SigV4 on `MOVE`, a bare subresource URL, S3 XML error parsing, the IAM form POST, Stats key-pair auth with `None` parameters dropped, WACM Basic auth and JSON errors, and missing-credential messages;
- `server_info`;
- every listed tool declaring annotations.

### 4.4 CI

`.github/workflows/ci.yml` has two jobs.
- **Lint and test:** runs on pull requests, pushes to `main` and manual dispatch. It runs `uv sync --locked`, `ruff check`, `ruff format --check` and `pytest -q`.
- **Live:** runs after lint and test. It isn't triggered by pull requests from forks, and it's skipped when the `WASABI_ACCESS_KEY_ID` secret isn't set. It runs with `WASABI_LIVE=1` and `WASABI_MCP_READ_ONLY=false`, using `uv run pytest -q -m live`.

Permissions are `contents: read`.

### 4.5 Endpoints

- S3: `https://s3.<region>.wasabisys.com`, path-style. `us-east-1` is also `s3.wasabisys.com`.
- IAM: `https://iam.wasabisys.com`. STS: `https://sts.wasabisys.com`.
- Stats: `https://stats.wasabisys.com`. Not `api.stats.wasabisys.com`, which doesn't resolve.
- WACM Connect: `https://api.wacm.wasabisys.com/api`.

## 5. Cross-cutting requirements

Every tool must meet all of these. The reviewer checks each one.

### 5.1 Naming and layout

- **Names** are snake_case, `<noun>_<verb>` or `<noun>_<sub-noun>_<verb>`: `bucket_list`, `object_tagging_set`, `iam_user_create`, `wacm_sub_account_list`. Use exactly the names in section 6.
- **Layout:** one module per group, `src/wasabi_mcp/tools/<module>.py`, using the module names in section 9. Tests go in `tests/tools/test_<module>.py`.
- **Descriptions:** each tool's docstring is its MCP description. Write one or two plain sentences saying what the tool does and what it returns. Mention `confirm` for destructive tools, and mention any Wasabi-specific behaviour an agent needs to know (for example, "Lifecycle rules take effect within 24–48 hours").

### 5.2 Kinds and safety

Every tool is one of three kinds, marked **R**, **W** or **D** in the catalogue.

| Kind | Annotation | Guard | Extra parameter |
|---|---|---|---|
| R (read) | `READ` | none | — |
| W (write) | `WRITE` | `require_writable(settings, "<Action>")` first | — |
| D (destructive) | `DESTRUCTIVE` | `require_confirmed(settings, "<Action>", confirm)` first | `confirm: bool = False` |

- **D covers** deletes, overwrites of existing configuration where data or access is lost, every change to policies, ACLs, access keys, login profiles and MFA, every change to retention or compliance, and anything else marked D in the catalogue.
- **Guards run before any network call.** A refused call never reaches Wasabi.
- **Secrets:**
  - Never return or log the configured access key, secret key or WACM API key.
  - Tools that create a new secret return it once, in the result field named in the catalogue, and only because they're D tools and so already need `confirm=true`. These are `iam_access_key_create`, `iam_login_profile_create`, `iam_mfa_virtual_device_create`, `sts_assume_role`, `sts_session_token` and `wacm_sub_account_create`.
  - Never pass `includeKeys=true` to WACM.

### 5.3 Inputs

- Use typed parameters with defaults. Names are snake_case: `bucket`, `key`, `prefix`, `version_id`, `region`, `max_items`, `continuation_token`.
- Take policy and JSON documents as `dict` or `str` (JSON). Validate the JSON before calling Wasabi.
- Take `region` as optional on bucket tools. The default is the settings region. Bucket operations must use the bucket's own region endpoint: a wrong-region endpoint allows GET but not PUT or DELETE. Tools that change a bucket look up its region with `GetBucketLocation` when `region` isn't given, and cache the answer.

### 5.4 Outputs

- Return JSON data: a dict for single results, or a dict holding a list plus pagination. Never return boto3 responses as they are; use `clean()`.
- **Lists** return `{"items": [...], "next_token": <str|null>, "truncated": <bool>}`. They take `max_items` (default 100, at most 1,000) and `continuation_token`.
- **Writes** return the resulting state, or `{"ok": true, ...}` with the identifiers the agent needs next.
- Sizes are in bytes, with a human-readable field alongside where useful, such as `size_human: "1.2 GB"`.

### 5.5 Errors

- Catch `ClientError` and `BotoCoreError` and raise `explain(exc, "<Action>")`. `rest.py` helpers already raise `ToolRefused`.
- Errors say what failed and why, using Wasabi's code and message (for example `ComplianceSettingsLocked`, `NoSuchBucketPolicy`). Add a fix where one is obvious.
- "Not found" on a read of optional configuration (no policy, no lifecycle, no CORS, no tagging) returns `{"configured": false}`, not an error.

### 5.6 Tests

For every tool:
1. a happy-path test;
2. for W and D tools, a test that the read-only server refuses without calling Wasabi;
3. for D tools, a test that `confirm=false` is refused;
4. an error-path test where Wasabi's documented behaviour has one.

Also:
- Use moto for what moto supports. Use respx with `rest_settings` for Wasabi extensions, Stats, WACM and anything moto doesn't support. Don't skip a tool because moto lacks it; mock it with respx instead.
- Live tests (`@pytest.mark.live`) go in `tests/live/`, for the flows named in each ticket. They create resources with a `wasabi-mcp-test-` prefix and always clean up, even on failure.
- `uv run ruff check . && uv run ruff format --check . && uv run pytest -q` must pass. This is dmx's `test_command` and CI.

### 5.7 Documentation

Each ticket from 1 to 23 documents its tools in `docs/tools/<module>.md`: one table with name, kind and description, in catalogue order. Ticket 24 assembles `docs/tools.md` from these files and checks it against the server's `list_tools`.

## 6. Tool catalogue

Kind: R read, W write, D destructive (see 5.2). "Ext" marks a Wasabi extension called through `rest.signed_s3` or `rest.iam_query`.

### 6.1 Buckets (`buckets.py`, 11 tools)

| Tool | Kind | API | Notes |
|---|---|---|---|
| `bucket_list` | R | ListBuckets | Each bucket's name and creation date. With `include_region=true`, also its region (GetBucketLocation per bucket). |
| `bucket_get` | R | HeadBucket, GetBucketLocation, GetBucketVersioning, GetObjectLockConfiguration | A summary: region, versioning, object lock enabled, and its default retention if any. |
| `bucket_create` | W | CreateBucket | `bucket`, `region`, `object_lock: bool` (also enables versioning; can't be undone, so say so in the description). Respects the 1,000-bucket limit error. |
| `bucket_delete` | D | DeleteBucket, `?force_delete=true` (Ext) | `force: bool = False` deletes all objects first (Wasabi `force_delete`). Compliance and Object Lock still apply. |
| `bucket_rename` | D | `MOVE /bucket` (Ext) | `Destination` header. Needs `s3:CreateBucket`. |
| `bucket_versioning_get` | R | GetBucketVersioning | Status and MFA Delete. |
| `bucket_versioning_set` | W | PutBucketVersioning | `enabled: bool`. Suspending is allowed; there's no way back to unversioned. |
| `bucket_tagging_get` | R | GetBucketTagging | `{"configured": false}` when none. |
| `bucket_tagging_set` | W | PutBucketTagging | Replaces the tag set. `tags: dict[str, str]`. |
| `bucket_tagging_delete` | D | DeleteBucketTagging | |
| `bucket_location_get` | R | GetBucketLocation | Region code and service URL. |

### 6.2 Object reads (`objects_read.py`, 6 tools)

| Tool | Kind | API | Notes |
|---|---|---|---|
| `object_list` | R | ListObjectsV2 | `prefix`, `delimiter` (returns `common_prefixes`), `start_after`, pagination. Each item: key, size, last modified, ETag. |
| `object_version_list` | R | ListObjectVersions | Versions and delete markers, with `key_marker` / `version_id_marker` pagination. |
| `object_get_info` | R | HeadObject | Size, content type, ETag, last modified, user metadata, version, and object lock retention and legal hold where present. |
| `object_get_text` | R | GetObject (ranged) | Returns text for objects up to `max_bytes` (default 256 KiB, at most 1 MiB). Refuses binary content; suggest `presign_get` instead. |
| `object_tagging_get` | R | GetObjectTagging | `version_id` optional. |
| `object_search` | R | ListObjectsV2 (paged) | Client-side filter over a prefix: `suffix`, `contains`, `min_size`, `max_size`, `modified_after`, `modified_before`. Scans at most `max_scan` keys (default 10,000) and reports when it stopped early. |

### 6.3 Object writes (`objects_write.py`, 7 tools)

| Tool | Kind | API | Notes |
|---|---|---|---|
| `object_put_text` | W | PutObject | Small text content (at most 1 MiB) with optional content type and metadata. `overwrite: bool = False` refuses when the key exists (HeadObject first). |
| `object_copy` | W | CopyObject | Within or across buckets in the same region. `overwrite` as above. Optional metadata replacement. |
| `object_delete` | D | DeleteObject | `version_id` optional. Without it on a versioned bucket, the result says a delete marker was created. |
| `object_delete_many` | D | DeleteObjects | Up to 1,000 keys, or `prefix` with `max_keys` (at most 10,000) to delete by prefix. Returns deleted and errors. |
| `object_tagging_set` | W | PutObjectTagging | Replaces the tag set. |
| `object_tagging_delete` | D | DeleteObjectTagging | |
| `object_metadata_update` | W | CopyObject to self, `MetadataDirective=REPLACE` | Changes content type, cache control and user metadata without re-uploading. |

### 6.4 Wasabi object extensions (`objects_ext.py`, 4 tools)

| Tool | Kind | API | Notes |
|---|---|---|---|
| `object_rename` | D | `MOVE /bucket/key` (Ext) | `destination` key, `overwrite: bool = False` (the `Overwrite` header). Needs `s3:PutObject`. |
| `object_rename_prefix` | D | `MOVE` with `X-Wasabi-Prefix: true` (Ext) | Renames every key under a prefix, all versions. Returns the per-key results Wasabi sends (XML parsed). |
| `object_compose` | W | `PUT ?compose` (Ext) | Links up to 32 source objects in the same bucket into one. No SSE-C. The ETag is a hash of the part MD5s; say so. |
| `object_append` | W | `PUT ?append` (Ext) | Appends text (at most 1 MiB) to an object. At most 1,023 appends per object. |

### 6.5 Presigned URLs and multipart (`transfer.py`, 8 tools)

| Tool | Kind | API | Notes |
|---|---|---|---|
| `presign_get` | R | presigned GetObject | `expires_seconds` from 60 to 604,800 (7 days, Wasabi's maximum for IAM-user URLs); default 3,600. |
| `presign_put` | W | presigned PutObject | Same limits. The URL lets a holder write, so it's W. |
| `presign_get_for_prefix` | R | ListObjectsV2 + presign | Presigned GET URLs for every key under `prefix` matching an optional `suffix`. At most `max_items` (default 100, at most 1,000). |
| `multipart_upload_list` | R | ListMultipartUploads | Includes initiation time. Note that Wasabi deletes incomplete uploads after about 31 days. |
| `multipart_part_list` | R | ListParts | |
| `multipart_upload_abort` | D | AbortMultipartUpload | |
| `multipart_upload_presign` | W | CreateMultipartUpload + presigned UploadPart | Starts an upload and returns `upload_id` and one presigned URL per part (`part_count` 1–10,000) so a client can upload data directly. |
| `multipart_upload_complete` | W | CompleteMultipartUpload | Takes `parts: [{part_number, etag}]`. |

### 6.6 Access: bucket policy and ACLs (`access.py`, 7 tools)

| Tool | Kind | API | Notes |
|---|---|---|---|
| `bucket_policy_get` | R | GetBucketPolicy | The document as JSON; `{"configured": false}` on NoSuchBucketPolicy. |
| `bucket_policy_set` | D | PutBucketPolicy | Validates JSON first. The description notes that AWS-style account and canonical IDs must be Wasabi's. |
| `bucket_policy_delete` | D | DeleteBucketPolicy | |
| `bucket_acl_get` | R | GetBucketAcl | Owner and grants. |
| `bucket_acl_set` | D | PutBucketAcl | `canned_acl` (private, public-read, public-read-write, authenticated-read) or explicit grants. Notes: trial accounts and accounts created after 2023-03-13 can't make buckets public; surface Wasabi's error as is. |
| `object_acl_get` | R | GetObjectAcl | |
| `object_acl_set` | D | PutObjectAcl | Same as `bucket_acl_set`, per object and version. |

### 6.7 CORS and lifecycle (`bucket_rules.py`, 6 tools)

| Tool | Kind | API | Notes |
|---|---|---|---|
| `bucket_cors_get` | R | GetBucketCors | `{"configured": false}` when none. Wasabi then returns permissive default CORS headers; say so in the description. |
| `bucket_cors_set` | W | PutBucketCors | Rules: allowed origins, methods, headers, expose headers, max age. |
| `bucket_cors_delete` | D | DeleteBucketCors | Restores Wasabi's default CORS behaviour. |
| `bucket_lifecycle_get` | R | GetBucketLifecycleConfiguration | |
| `bucket_lifecycle_set` | D | PutBucketLifecycleConfiguration | Replaces all rules. Supported actions: Expiration (days or date), NoncurrentVersionExpiration, ExpiredObjectDeleteMarker, AbortIncompleteMultipartUpload. Filters: prefix, tags, object size. Rejects transitions (Wasabi has one storage class) before calling Wasabi. The description notes that rules run daily at 00:00 UTC, take 24–48 hours to apply, can't delete locked objects, and can't be set while MFA Delete is on. |
| `bucket_lifecycle_delete` | D | DeleteBucketLifecycle | |

### 6.8 Object Lock and retention (`object_lock.py`, 6 tools)

| Tool | Kind | API | Notes |
|---|---|---|---|
| `bucket_object_lock_get` | R | GetObjectLockConfiguration | Enabled, and the default rule (mode, days or years). |
| `bucket_object_lock_set` | D | PutObjectLockConfiguration | Default rule: `mode` COMPLIANCE or GOVERNANCE, and `days` or `years` (not both). `clear_default=true` sends `Enabled` only, removing the default rule. Object Lock can only be enabled at bucket creation; say so. |
| `object_retention_get` | R | GetObjectRetention | |
| `object_retention_set` | D | PutObjectRetention | `mode`, `retain_until` (ISO 8601). `bypass_governance: bool = False` sends `x-amz-bypass-governance-retention`. Compliance mode can't be shortened by anyone; say so. |
| `object_legal_hold_get` | R | GetObjectLegalHold | |
| `object_legal_hold_set` | D | PutObjectLegalHold | `on: bool`. |

### 6.9 Wasabi compliance (`compliance.py`, 4 tools, all Ext)

Wasabi Compliance is a Wasabi-only alternative to Object Lock. A bucket has one or the other.

| Tool | Kind | API | Notes |
|---|---|---|---|
| `bucket_compliance_get` | R | `GET /bucket?compliance` | Status, LockTime, RetentionDays, ConditionalHold, IsLocked, DeleteAfterRetention. |
| `bucket_compliance_set` | D | `PUT /bucket?compliance` | `enabled`, `retention_days` (required), `conditional_hold`, `lock_time` (ISO date, `now` or `off`), `delete_after_retention`. Once locked, settings can't be reduced without Wasabi Support; say so and surface ComplianceSettingsLocked. |
| `object_compliance_get` | R | `GET /bucket/key?compliance` | Includes the object SHA256 Wasabi returns (not for multipart or composed objects). |
| `object_compliance_set` | D | `PUT /bucket/key?compliance` | `release_conditional_hold` (release only), `legal_hold`, `retention_time` (extend only). Reject a shorter retention before calling Wasabi. |

### 6.10 Logging, inventory and replication (`bucket_ops.py` and `bucket_replication.py`, 10 tools)

| Tool | Kind | API | Notes |
|---|---|---|---|
| `bucket_logging_get` | R | GetBucketLogging | |
| `bucket_logging_set` | W | PutBucketLogging | `target_bucket` (same account; surface LoggingBucketSameOwner), `target_prefix`; `enabled=false` turns it off. Best effort, with no SLA; say so. |
| `bucket_inventory_list` | R | `GET /bucket?inventory` (Ext) | Lists configurations. |
| `bucket_inventory_get` | R | `GET /bucket?inventory&id=` (Ext) | |
| `bucket_inventory_set` | W | `PUT /bucket?inventory&id=` (Ext) | CSV only. Daily or Weekly; Current or All versions; prefix filter; destination bucket and prefix; optional fields from Wasabi's list (Size, LastModifiedDate, ETag, StorageClass, IsMultipartUploaded, ReplicationStatus, EncryptionStatus, ObjectLockRetainUntilDate, ObjectLockMode, ObjectLockLegalHoldStatus, ChecksumAlgorithm, ObjectAccessControlList, ObjectOwner). |
| `bucket_inventory_delete` | D | `DELETE /bucket?inventory&id=` (Ext) | |
| `bucket_replication_get` | R | GetBucketReplication | |
| `bucket_replication_set` | D | PutBucketReplication | Rules with prefix and/or tag filters, destination bucket (ARN `arn:aws:s3:::name`), delete marker replication on or off. Same-account: empty role. Cross-account: role ARN, destination account and owner translation. Check that both buckets have the same versioning state first and explain a mismatch. |
| `bucket_replication_delete` | D | DeleteBucketReplication | |
| `bucket_replication_backfill` | W | `PUT /bucket?manifestCreateAndProcess` (Ext) | Replicates existing objects, which replication alone doesn't. Returns the job ID. |

### 6.11 IAM: users, login profiles and access keys (`iam_users.py` and `iam_keys.py`, 14 tools)

| Tool | Kind | API | Notes |
|---|---|---|---|
| `iam_user_list` | R | ListUsers | Path prefix and pagination. |
| `iam_user_get` | R | GetUser, ListGroupsForUser, ListAttachedUserPolicies, ListUserPolicies, ListAccessKeys | One summary of a user: groups, attached and inline policies, and keys (IDs and status only). |
| `iam_user_create` | W | CreateUser | `user_name`, `path`. |
| `iam_user_update` | W | UpdateUser | New name and/or path. |
| `iam_user_delete` | D | DeleteUser | `cascade: bool = False` first removes the user's keys, login profile, group memberships, attached and inline policies and MFA devices, listing each step in the result. Without it, explain what blocks the delete. |
| `iam_login_profile_get` | R | GetLoginProfile | `{"configured": false}` when none. |
| `iam_login_profile_create` | D | CreateLoginProfile | Generates a strong password when none is given. Returns it once as `password`. `password_reset_required` defaults to true. |
| `iam_login_profile_update` | D | UpdateLoginProfile | |
| `iam_login_profile_delete` | D | DeleteLoginProfile | |
| `iam_access_key_list` | R | ListAccessKeys | `user_name` optional; `all_users=true` uses Wasabi's `UserName=*` (root shows a blank name). IDs, status and created date; never secrets. |
| `iam_access_key_create` | D | CreateAccessKey | Returns `access_key_id` and `secret_access_key` once, with a note that the secret can't be retrieved again. |
| `iam_access_key_update` | D | UpdateAccessKey | `active: bool`. |
| `iam_access_key_delete` | D | DeleteAccessKey | |
| `iam_access_key_last_used` | R | GetAccessKeyLastUsed | |

### 6.12 IAM: groups, roles, policies, account and STS

**Groups (`iam_groups.py`, 8 tools)**

| Tool | Kind | API | Notes |
|---|---|---|---|
| `iam_group_list` | R | ListGroups | |
| `iam_group_get` | R | GetGroup, ListAttachedGroupPolicies, ListGroupPolicies | Members and policies. |
| `iam_group_create` | W | CreateGroup | |
| `iam_group_update` | W | UpdateGroup | |
| `iam_group_delete` | D | DeleteGroup | `cascade` removes members and policies first, as for users. |
| `iam_group_add_user` | D | AddUserToGroup | |
| `iam_group_remove_user` | D | RemoveUserFromGroup | |
| `iam_user_group_list` | R | ListGroupsForUser | |

**Roles and STS (`iam_roles.py`, 8 tools)**

| Tool | Kind | API | Notes |
|---|---|---|---|
| `iam_role_list` | R | ListRoles | |
| `iam_role_get` | R | GetRole, ListAttachedRolePolicies, ListRolePolicies | Includes the trust policy as JSON. |
| `iam_role_create` | D | CreateRole | `assume_role_policy` (dict or JSON), `description`, `max_session_duration` (at most 43,200). |
| `iam_role_delete` | D | DeleteRole | `cascade` detaches and deletes policies first. |
| `iam_role_trust_policy_update` | D | UpdateAssumeRolePolicy | |
| `sts_caller_identity` | R | GetCallerIdentity | Account, user ID, ARN. |
| `sts_assume_role` | D | AssumeRole | `role_arn`, `session_name`, `duration_seconds` (at most 43,200), optional inline `policy`. Returns the temporary credentials once. Wasabi doesn't allow root to assume roles; explain the error. |
| `sts_session_token` | D | GetSessionToken | Optional MFA serial and code. Returns the temporary credentials once. |

**Managed and inline policies (`iam_policies.py` and `iam_policy_bindings.py`, 18 tools)**

| Tool | Kind | API | Notes |
|---|---|---|---|
| `iam_policy_list` | R | ListPolicies | `scope` All, AWS (Wasabi system policies) or Local; `only_attached`. |
| `iam_policy_get` | R | GetPolicy + GetPolicyVersion (default) | Includes the default version's document as JSON. |
| `iam_policy_create` | D | CreatePolicy | |
| `iam_policy_delete` | D | DeletePolicy | `cascade` detaches from all entities and deletes non-default versions first. |
| `iam_policy_version_list` | R | ListPolicyVersions | |
| `iam_policy_version_get` | R | GetPolicyVersion | |
| `iam_policy_version_create` | D | CreatePolicyVersion | `set_as_default`. When five versions exist, explain that one must be deleted. |
| `iam_policy_version_delete` | D | DeletePolicyVersion | |
| `iam_policy_version_set_default` | D | SetDefaultPolicyVersion | |
| `iam_policy_entities_list` | R | ListEntitiesForPolicy | Users, groups and roles. |
| `iam_policy_attach` | D | AttachUserPolicy / AttachGroupPolicy / AttachRolePolicy | `principal_type` user, group or role; `principal_name`; `policy_arn`. |
| `iam_policy_detach` | D | Detach*Policy | Same parameters. |
| `iam_attached_policy_list` | R | ListAttached*Policies | Same `principal_type`. |
| `iam_inline_policy_list` | R | List*Policies | Same `principal_type`. |
| `iam_inline_policy_get` | R | Get*Policy | The document as JSON. |
| `iam_inline_policy_put` | D | Put*Policy | |
| `iam_inline_policy_delete` | D | Delete*Policy | |
| `iam_policy_validate` | R | `ValidatePolicy` (Ext, `rest.iam_query`) | Lints a policy and evaluates it for the signer: `policy_document`, `is_principal`, optional `use_action`, `use_resource`, `use_service` (iam, sts or s3). Returns ALLOW, DENY or no result, plus any lint messages. |

**Account settings and MFA (`iam_account.py`, 12 tools)**

| Tool | Kind | API | Notes |
|---|---|---|---|
| `iam_account_summary` | R | GetAccountSummary | |
| `iam_account_authorization_details` | R | GetAccountAuthorizationDetails | `filter`: User, Role, Group, LocalManagedPolicy, AWSManagedPolicy. Paginated. |
| `iam_password_policy_get` | R | GetAccountPasswordPolicy | `{"configured": false}` when none. |
| `iam_password_policy_set` | D | UpdateAccountPasswordPolicy | |
| `iam_password_policy_delete` | D | DeleteAccountPasswordPolicy | |
| `iam_account_alias_list` | R | ListAccountAliases | |
| `iam_account_alias_create` | W | CreateAccountAlias | |
| `iam_account_alias_delete` | D | DeleteAccountAlias | |
| `iam_mfa_device_list` | R | ListMFADevices, ListVirtualMFADevices | `user_name` optional; `assignment_status` for virtual devices. |
| `iam_user_mfa_get` | R | `GetUserMFA` (Ext, `rest.iam_query`) | Device details, plus the authentication date and age of the current credentials. |
| `iam_mfa_virtual_device_create` | D | CreateVirtualMFADevice | Returns the Base32 seed and a QR PNG (base64) once, for the user to enrol. Wasabi supports virtual TOTP devices only. |
| `iam_mfa_device_deactivate` | D | DeactivateMFADevice (+ DeleteVirtualMFADevice with `delete=true`) | |

### 6.13 Usage: Stats API (`stats.py`, 4 tools)

All four use `rest.stats_get`, with `Authorization: <key>:<secret>`. The keys must be root, or a sub-user with WasabiAccountStatsAccess or WasabiBucketStatsAccess. Explain a 401/403 in those terms.

| Tool | Kind | API | Notes |
|---|---|---|---|
| `stats_account_utilization_list` | R | `GET /v1/standalone/utilizations` | `from_date`, `to_date` (YYYY-MM-DD; Wasabi defaults to the last month), `latest`, `include_regional`, `page_num`, `page_size` (at most 100). Records as Wasabi returns them, plus `*_human` sizes for storage fields. |
| `stats_bucket_utilization_list` | R | `GET /v1/standalone/utilizations/bucket` | Per-bucket records. RecordCount and PageCount come only on page 0; carry them forward. |
| `stats_bucket_utilization_get` | R | `/v1/standalone/utilizations/bucket/{bucketName}` or `.../{bucketNumber}` | `bucket` (name) or `bucket_number`, `latest`, date range. |
| `stats_usage_summary` | R | the two lists above | The latest day: total active storage, deleted storage, objects, API calls, upload and download bytes, and the top `top_n` buckets by active storage. One call for "how much am I using?". |

Metric notes for descriptions:
- Stats are computed daily at 00:00 UTC.
- Storage fields are point-in-time; byte and call counters are per day.
- Padded size applies the 4 KB minimum object size.
- Deleted storage reflects minimum-retention billing.

### 6.14 Account governance: WACM Connect (`wacm_*.py`, 32 tools)

All use `rest.wacm_request` with HTTP Basic (`WASABI_WACM_USERNAME`, `WASABI_WACM_API_KEY`). List tools take `page` and `size`. Rate limits are per control account per minute: 1,000 GET, 100 PUT, 100 POST, 10 DELETE. On HTTP 429, retry once after the `Retry-After` header (default 5 s), then report it. Never send `includeKeys`.

**Accounts and control accounts (`wacm_accounts.py`, 9 tools)**

| Tool | Kind | API |
|---|---|---|
| `wacm_standalone_account_list` | R | GET /v1/accounts (filters: id, name, email, status, wasabiAccountNumber, partnerName, companyName) |
| `wacm_standalone_account_create` | W | POST /v1/accounts (firstName, lastName, email, country, storageAmount band; companyName, phoneNumber) |
| `wacm_storage_amount_list` | R | GET /v1/accounts/storage-amounts |
| `wacm_country_list` | R | GET /v1/accounts/countries |
| `wacm_control_account_list` | R | GET /v1/control-accounts (never `includeApiKey`) |
| `wacm_control_account_get` | R | GET /v1/control-accounts/{id} |
| `wacm_control_account_usage_list` | R | GET /v1/control-accounts/usages (governanceAccountId, controlAccountId, subAccountId, wasabiAccountNumber, latest, from, to) |
| `wacm_control_account_usage_get` | R | GET /v1/control-accounts/usages/{utilizationId} |
| `wacm_control_account_bucket_list` | R | GET /v1/control-accounts/{id}/buckets (from, to, latest, name, bucketNumber, region) |

**Channel accounts and their users (`wacm_channels.py`, 8 tools)**

| Tool | Kind | API |
|---|---|---|
| `wacm_channel_account_list` | R | GET /v1/channel-accounts |
| `wacm_channel_account_get` | R | GET /v1/channel-accounts/{id} |
| `wacm_channel_account_create` | W | POST /v1/channel-accounts (name, contactEmail, purchasedStorage, storageQuotaType) |
| `wacm_channel_account_update` | W | PUT /v1/channel-accounts/{id} |
| `wacm_channel_account_delete` | D | DELETE /v1/channel-accounts/{id} (also unlinks its sub-accounts; say so) |
| `wacm_channel_user_create` | W | POST /v1/channel-accounts/users |
| `wacm_channel_user_update` | D | PUT /v1/channel-accounts/users/{userId} (role, active, MFA and password changes) |
| `wacm_channel_user_delete` | D | DELETE /v1/channel-accounts/users/{userId} |

**Sub-accounts (`wacm_sub_accounts.py`, 6 tools)**

| Tool | Kind | API | Notes |
|---|---|---|---|
| `wacm_sub_account_list` | R | GET /v1/sub-accounts | Filters: id, name, controlAccountId, governanceAccountId, status (ON_TRIAL, PAID_ACCOUNT, SUSPENDED, DEACTIVATED), includeDeleted. Never `includeKeys`. |
| `wacm_sub_account_get` | R | GET /v1/sub-accounts/{id} | Never `includeKeys`. |
| `wacm_sub_account_create` | D | POST /v1/sub-accounts | name (3–100 characters), wasabiAccountEmail, password or sendWelcomeEmail, controlAccountId, channelAccountId, isTrial, trialQuotaTB, trialDays, purchasedStorageTB, storageQuotaType (Soft or Hard; Hard needs purchasedStorageTB). Returns the new account's `access_key_id` and `secret_access_key` once. |
| `wacm_sub_account_update` | D | PATCH /v1/sub-accounts/{id} | Partial update. Includes `active` (enable or disable), trialToPaid, quota, resetKeys, resetMFA. |
| `wacm_sub_account_delete` | D | DELETE /v1/sub-accounts/{id} | |
| `wacm_sub_account_bucket_list` | R | GET /v1/sub-accounts/{id}/buckets | from, to, latest, name, bucketNumber, region. |

**Members, invoices and usages (`wacm_members.py` with 5 tools, `wacm_billing.py` with 4 tools)**

| Tool | Kind | API |
|---|---|---|
| `wacm_member_list` | R | GET /v1/members (id, status, username, subAccountId) |
| `wacm_member_get` | R | GET /v1/members/{id} |
| `wacm_member_create` | D | POST /v1/members (firstName, lastName, email, password, memberRole, username, passwordResetRequired) |
| `wacm_member_update` | D | PUT /v1/members/{id} (role, MFA, password, active, reinstate) |
| `wacm_member_delete` | D | DELETE /v1/members/{id} |
| `wacm_invoice_list` | R | GET /v1/invoices (governance, control and sub-account IDs, invoice IDs, wasabiAccountNumber, latest, from, to) |
| `wacm_invoice_get` | R | GET /v1/invoices/{id} |
| `wacm_usage_list` | R | GET /v1/usages (same filters as invoices) |
| `wacm_usage_get` | R | GET /v1/usages/{utilizationId} |

### 6.15 Catalogue totals

| Area | Groups | Tools |
|---|---|---|
| Storage (S3 and Wasabi extensions) | 6.1–6.10 | 69 |
| Identity (IAM and STS) | 6.11–6.12 | 60 |
| Usage (Stats) | 6.13 | 4 |
| Account governance (WACM Connect) | 6.14 | 32 |
| Server | `meta` | 1 |
| **Total** | | **166** |

**Out of scope, because Wasabi documents them as unsupported:**
- transfer acceleration, analytics, metrics and intelligent-tiering configurations;
- default bucket encryption and SSE-KMS;
- metadata tables, ownership controls and requester pays;
- website hosting and Block Public Access / GetBucketPolicyStatus;
- GetObjectAttributes, GetObjectTorrent, RestoreObject, WriteGetObjectResponse and SelectObjectContent;
- CreateSession, batch operations and browser POST uploads.

## 7. Configuration

Environment variables only, read once by `Settings.from_env()` (section 4.2).

| Variable | Default | Meaning |
|---|---|---|
| `WASABI_ACCESS_KEY_ID`, `WASABI_SECRET_ACCESS_KEY` | — | The key pair used for S3, IAM, STS and Stats. |
| `WASABI_REGION` | `us-east-1` | The default region. |
| `WASABI_MCP_READ_ONLY` | `true` | W and D tools refuse while true. |
| `WASABI_WACM_USERNAME`, `WASABI_WACM_API_KEY` | — | WACM Connect (account or user API key). S3 keys don't work for WACM. |
| `WASABI_S3_ENDPOINT`, `WASABI_IAM_ENDPOINT`, `WASABI_STS_ENDPOINT`, `WASABI_STATS_ENDPOINT`, `WASABI_WACM_ENDPOINT` | Wasabi's public endpoints | Overrides. |

## 8. Acceptance criteria

1. `list_tools` returns exactly the 166 tools in section 6, with the names, kinds (annotations) and groups given there. A test in the final ticket checks this against a list in `tests/catalogue.py`.
2. Every W and D tool refuses on a read-only server, and every D tool refuses without `confirm=true`. Tests prove both, with no network call made.
3. Every tool has a happy-path test. The suite passes in CI.
4. Live tests pass against a real Wasabi account for:
   - bucket create, versioning, tagging and delete;
   - object put, get, copy, rename and delete;
   - presigned GET;
   - lifecycle set, get and delete;
   - IAM user, group, policy, attach and delete;
   - Stats summary.
5. No tool output contains the configured secret key or WACM API key. A test calls every R tool with a recognisable secret in settings and checks the outputs.
6. `docs/tools.md` lists every tool. The README quick start works from a clean clone.
7. Tickets 1–23 don't change files outside their own module(s), tests and docs file. A ticket that needs a shared helper adds it in its own module.

## 9. Delivery plan (tickets)

Each ticket is one PR from one branch, delivering whole tool groups with their tests and their `docs/tools/<module>.md` file.

**Two stages:**
- **Stage 1** is ticket 0, the foundation in section 4. It merges first.
- **Stage 2** is tickets 1–24, which start once ticket 0 is on `main`.
  - Tickets 1–23 are independent and run in parallel. Each adds only its own new module(s), test file(s) and `docs/tools/<module>.md`. None edits a shared file, so they never conflict.
  - Ticket 24 runs last.

| # | Ticket | Module(s) | Tools |
|---|---|---|---|
| 0 | Foundation: project, config, clients, safety, request helpers, test harness, CI, `server_info` (section 4) | `pyproject.toml`, `src/wasabi_mcp/*`, `tests/conftest.py`, `tests/helpers.py`, `tests/test_*.py`, `.github/workflows/ci.yml` | 1 |
| 1 | Bucket tools | `buckets.py` | 11 |
| 2 | Object read tools | `objects_read.py` | 6 |
| 3 | Object write tools | `objects_write.py` | 7 |
| 4 | Wasabi object extensions (MOVE, compose, append) | `objects_ext.py` | 4 |
| 5 | Presigned URLs and multipart uploads | `transfer.py` | 8 |
| 6 | Bucket policy and ACL tools | `access.py` | 7 |
| 7 | CORS and lifecycle tools | `bucket_rules.py` | 6 |
| 8 | Object Lock, retention and legal hold | `object_lock.py` | 6 |
| 9 | Wasabi Compliance tools | `compliance.py` | 4 |
| 10 | Bucket logging and inventory | `bucket_ops.py` (logging, inventory) | 6 |
| 11 | Bucket replication | `bucket_replication.py` | 4 |
| 12 | IAM users and login profiles | `iam_users.py` | 9 |
| 13 | IAM access keys | `iam_keys.py` | 5 |
| 14 | IAM groups | `iam_groups.py` | 8 |
| 15 | IAM roles and STS | `iam_roles.py` | 8 |
| 16 | IAM managed policies and versions | `iam_policies.py` | 10 |
| 17 | IAM policy attachment, inline policies and ValidatePolicy | `iam_policy_bindings.py` | 8 |
| 18 | IAM account settings and MFA | `iam_account.py` | 12 |
| 19 | Stats API usage tools | `stats.py` | 4 |
| 20 | WACM accounts and control accounts | `wacm_accounts.py` | 9 |
| 21 | WACM channel accounts and users | `wacm_channels.py` | 8 |
| 22 | WACM sub-accounts | `wacm_sub_accounts.py` | 6 |
| 23 | WACM members, invoices and usages | `wacm_members.py`, `wacm_billing.py` | 9 |
| 24 | Catalogue check, secret-leak test, live suite and docs | `tests/catalogue.py`, `tests/test_catalogue.py`, `tests/live/`, `docs/tools.md`, README | — |

Notes on the module split:
- Tickets 10 and 11 split section 6.10 into `bucket_ops.py` (logging and inventory) and `bucket_replication.py`.
- Tickets 12 and 13 split section 6.11 into `iam_users.py` and `iam_keys.py`.
- Tickets 16 and 17 split the policies table into `iam_policies.py` (the first ten tools) and `iam_policy_bindings.py` (attach, detach, attached and inline lists, inline get/put/delete, validate).
- Tool names and kinds don't change.

**Live tests:** tickets 1–23 add unit tests only. Ticket 24 adds the live suite in section 8.4.

## 10. Risks and decisions

| Item | Decision |
|---|---|
| Wasabi's docs disagree on S3 CORS support ("not supported" on the operations page; "fully supported" on the newer CORS page). | Implement Put/Get/DeleteBucketCors (6.7). Mark CORS for verification in the live suite. |
| Event notifications are Console only (AWS SNS), with no documented API. | Out of scope. |
| The WACM API key is all-powerful for the account hierarchy. | WACM tools are off unless both WACM variables are set. Writes follow 5.2. |
| Tools that create credentials return secrets. | Only D tools do, only once, and only in the named field. The secret-leak test (section 8.5) covers all R tools. |
| moto doesn't model Wasabi extensions or Wasabi-only behaviour. | Mock those with respx (`rest_settings`). Live tests cover the core flows. |
| Region-bound bucket writes fail on the wrong endpoint. | Bucket write tools resolve the bucket's region (5.3). |

## 11. Out of scope for this release

- MCP resources and prompts. Tools only for now.
- A hosted HTTP deployment with auth. Streamable HTTP is local or trusted-network only.
- Packaging to PyPI, and Docker images.

## 12. References

All public:
- Wasabi MCP for AI: https://wasabi.com/innovation-labs/mcp-for-ai
- Hosted Wasabi MCP: https://docs.wasabi.com/docs/hosted-wasabi-mcp-with-ai-systems
- S3 API reference: https://docs.wasabi.com/apidocs/wasabi-api
- Bucket operations: https://docs.wasabi.com/apidocs/operations-on-buckets-1
- Object operations: https://docs.wasabi.com/apidocs/operations-on-objects
- Object Lock: https://docs.wasabi.com/apidocs/object-lock-on-buckets-with-the-wasabi-s3-api
- Compliance: https://docs.wasabi.com/apidocs/compliance-with-the-wasabi-s3-api
- Inventory: https://docs.wasabi.com/apidocs/inventory-configuration-management
- Replication: https://docs.wasabi.com/apidocs/object-replication-api
- Lifecycle: https://docs.wasabi.com/apidocs/lifecycle-policy
- Presigned URLs: https://docs.wasabi.com/docs/how-do-i-generate-pre-signed-urls-for-temporary-access-with-wasabi
- Service URLs and regions: https://docs.wasabi.com/docs/service-urls-for-wasabis-storage-regions-2
- IAM and STS: https://docs.wasabi.com/apidocs/iam-and-sts-support
- Stats API: https://docs.wasabi.com/apidocs/wasabi-stats-api
- WACM Connect API: https://docs.wasabi.com/apidocs/wacm-connect-api

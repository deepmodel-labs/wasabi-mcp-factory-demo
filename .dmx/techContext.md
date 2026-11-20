# Tech context

## Stack (decided, per docs/prd.md section 4)
- Python 3.12+, managed with uv. Commit `uv.lock`; CI runs `uv sync --locked`.
- MCP Python SDK `mcp` 2.x. The server class is `mcp.server.mcpserver.MCPServer`; FastMCP is the 1.x name and doesn't exist in 2.x. Tool annotations use snake_case fields (`read_only_hint`, `destructive_hint`, `open_world_hint`). `call_tool` returns a `CallToolResult` with `structured_content`, `content` and `is_error`.
- boto3/botocore for S3, IAM and STS; httpx for Wasabi's REST APIs and S3/IAM extensions (signed with `botocore.auth.SigV4Auth`).
- Dev: pytest with pytest-asyncio (`asyncio_mode = auto`), moto with server mode, respx, ruff (line length 100).
- mcp 2.3.0 error handling: a tool exception reaches the client with its message only if it subclasses `mcp.server.mcpserver.exceptions.ToolError`, so `ToolRefused` does. In-process `MCPServer.call_tool` raises `ToolError` (`"Error executing tool <name>: <msg>"`) rather than returning `is_error`; `ToolAnnotations` is in `mcp.types`.
- mcp 2.3.0 stdio client: `mcp.client.stdio.stdio_client(StdioServerParameters(...))` with `mcp.client.session.ClientSession`. `ThreadedMotoServer(port=0).get_host_and_port()` gives the bound port.
- respx matches routes in registration order: register path-specific routes before broad `host=` routes.
- httpx renders Python `True` query values as `True`; for `rest.stats_get` / `wacm_request` params send booleans as the strings `"true"`/`"false"`.
- Tool error messages in tests carry the SDK prefix `"Error executing tool <name>: "`, so `pytest.raises(match=...)` patterns must not anchor with `^`.
- moto rejects bucket names under 3 characters, and returns 200 (not BucketAlreadyOwnedByYou) when re-creating an owned us-east-1 bucket.
- respx only intercepts httpx; boto3 uses urllib3. Tests of `rest.signed_s3` tools on `rest_server` pass `region` explicitly so no boto3 GetBucketLocation lookup runs.
- moto ignores explicit grants on PutObjectAcl (only canned ACLs take effect). To test them, register the group on a manually built `MCPServer` with its own `Clients` and capture the request with a botocore `before-call.s3.PutObjectAcl` hook.
- moto 5.2.5: GetObjectRetention always returns HTTP 500, and PutObjectLockConfiguration with a default rule needs an empty bucket. Check retention through HeadObject (`ObjectLockMode`, `ObjectLockRetainUntilDate`) and stub reads with `botocore.stub.Stubber` on a manually registered server's `clients.s3()`.
- moto PutBucketLogging needs the target bucket to grant log delivery: create it with `ACL="log-delivery-write"` in tests.
- moto drops the replication Role, `And` filters and DeleteMarkerReplication status when read back. Check the sent parameters with a botocore `provide-client-params.s3.PutBucketReplication` hook instead.
- Wasabi doesn't document the response shape of `PUT /bucket?manifestCreateAndProcess` (replication backfill). `bucket_replication_backfill` reads a JobId (or Id) from either JSON or XML. Confirm the real shape in the live suite (ticket 24).
- Wasabi Stats API: query names are `from`, `to` (YYYY-MM-DD), `latest`, `includeRegional`, `pageNum`, `pageSize` (max 100). Responses are `{Records, RecordCount, PageCount}` with the counts only on page 0. Bucket records name the bucket in `Bucket`; `PaddedStorageSizeBytes` is treated as active storage. Confirm in the live suite (ticket 24).
- For Wasabi-only behaviour on IAM/STS boto3 calls (which respx can't see), use botocore's `Stubber`; respx only for `rest.py` helpers.
- moto 5.2 IAM gaps: ignores `MaxItems`/`Marker` on ListUsers/ListGroups and `PathPrefix` on ListGroups, never raises `DeleteConflict`, doesn't error on UpdateGroup for a missing group, and reports `PasswordResetRequired=false` whatever was sent.
- botocore already URL-decodes and parses IAM policy documents (its `json_decode_policies` handler), so boto3 returns them as dicts.
- moto 5.2 deletes a managed policy that's still attached (no `DeleteConflict`), but does raise `DeleteConflict` for DeleteRole with attached policies and `LimitExceeded` on a sixth policy version.
- `tests.helpers.call` unwraps a result that is exactly `{"result": ...}`, so a tool must not return a dict whose only key is `result`.
- moto 5.2 ListVirtualMFADevices returns each device's `Base32StringSeed` and `QRCodePNG` (as bytes); tools that list MFA devices must strip them.
- Wasabi's ValidatePolicy and GetUserMFA response shapes aren't documented in the PRD: the tools return the `<ActionResult>` element as JSON, and unit tests use plausible XML. Live tests should confirm the real shapes.
- WACM Connect has no live tests: it needs a partner control account. Its page envelope field names and where `wacm_sub_account_create` puts the new keys are guessed from the docs, so `_wacm.page_result` and the create tool accept several variants. `rest.wacm_request` doesn't expose `Retry-After`, so the 429 retry always waits 5 s.

## Dev setup (once ticket 0 lands)
- `uv sync`
- `uv run ruff check . && uv run ruff format --check . && uv run pytest -q`: the same as CI and dmx's `test_command`.
- Live: `WASABI_LIVE=1 WASABI_MCP_READ_ONLY=false uv run pytest -q -m live`, with Wasabi keys set.

## Constraints
- Wasabi endpoints: `https://s3.<region>.wasabisys.com` (path-style), `https://iam.wasabisys.com`, `https://sts.wasabisys.com`, `https://stats.wasabisys.com` (not `api.stats...`), and `https://api.wacm.wasabisys.com/api`.
- Build only from public documentation. Never copy code from Wasabi's official MCP server or any third-party Wasabi MCP project.
- Never log or return the configured credentials.
- The factory runner can't write `.github/` (it's mounted as /dev/null). Changes to `.github/workflows/ci.yml` go to a maintainer: put the exact YAML in the job folder and the PR description.
- A ranged GetObject on an empty object fails with `InvalidRange`; treat that as an empty body.
- Pushed feature branches are never force-pushed. To bring one up to date with `main` (even when a review asks for a rebase), merge `origin/main` into it.
- moto 5.2.5 has no GetObjectRetention handler (it returns 500) and doesn't enforce `x-amz-bypass-governance-retention`. Test those paths by patching `Clients._client` to register botocore `before-call` (canned response) or `before-send` (header capture) hooks; respx can't intercept boto3.
- moto multipart: presigned part PUTs sent with urllib need an explicit non-form `Content-Type` (else Flask parses the body as a form and loses `partNumber`), ListMultipartUploads ignores `MaxUploads`, and CompleteMultipartUpload with an unknown upload id returns 500. Test paging with botocore `before-parameter-build`/`after-call` hooks.
- `tests.helpers.call(server, name, /, **arguments)` takes `server` and `name` positional-only, so tools with their own `name` parameter can be called. `tests/catalogue.py` is the PRD section 6 catalogue that `tests/test_catalogue.py` checks the server against; add a row there with every new tool.
- Canned (empty) botocore responses crash many tools' parsers. For checks that must reach every R tool, run against moto seeded with resources named like the dummy arguments, and respx for Stats/WACM.
- Live suite (`tests/live/`): resources come from the `new_name` fixture (`wasabi-mcp-test-<id>`), which records them for a session finalizer that deletes them with boto3 and fails the run if any remain. It can be dry-run against moto by setting `WASABI_LIVE=1`, fake keys and `WASABI_S3/IAM/STS_ENDPOINT` to a moto server; only `object_rename` (MOVE) and the Stats tests fail there.

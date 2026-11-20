# Tech context

## Stack (decided, per docs/prd.md section 4)
- Python 3.12+, managed with uv. Commit `uv.lock`; CI runs `uv sync --locked`.
- MCP Python SDK `mcp` 2.x. The server class is `mcp.server.mcpserver.MCPServer`; FastMCP is the 1.x name and doesn't exist in 2.x. Tool annotations use snake_case fields (`read_only_hint`, `destructive_hint`, `open_world_hint`). `call_tool` returns a `CallToolResult` with `structured_content`, `content` and `is_error`.
- boto3/botocore for S3, IAM and STS; httpx for Wasabi's REST APIs and S3/IAM extensions (signed with `botocore.auth.SigV4Auth`).
- Dev: pytest with pytest-asyncio (`asyncio_mode = auto`), moto with server mode, respx, ruff (line length 100).
- mcp 2.3.0 error handling: a tool exception reaches the client with its message only if it subclasses `mcp.server.mcpserver.exceptions.ToolError`, so `ToolRefused` does. In-process `MCPServer.call_tool` raises `ToolError` (`"Error executing tool <name>: <msg>"`) rather than returning `is_error`; `ToolAnnotations` is in `mcp.types`.
- mcp 2.3.0 stdio client: `mcp.client.stdio.stdio_client(StdioServerParameters(...))` with `mcp.client.session.ClientSession`. `ThreadedMotoServer(port=0).get_host_and_port()` gives the bound port.
- respx matches routes in registration order: register path-specific routes before broad `host=` routes.
- Tool error messages in tests carry the SDK prefix `"Error executing tool <name>: "`, so `pytest.raises(match=...)` patterns must not anchor with `^`.
- moto rejects bucket names under 3 characters, and returns 200 (not BucketAlreadyOwnedByYou) when re-creating an owned us-east-1 bucket.
- respx only intercepts httpx; boto3 uses urllib3. Tests of `rest.signed_s3` tools on `rest_server` pass `region` explicitly so no boto3 GetBucketLocation lookup runs.
- moto ignores explicit grants on PutObjectAcl (only canned ACLs take effect). To test them, register the group on a manually built `MCPServer` with its own `Clients` and capture the request with a botocore `before-call.s3.PutObjectAcl` hook.
- moto 5.2.5: GetObjectRetention always returns HTTP 500, and PutObjectLockConfiguration with a default rule needs an empty bucket. Check retention through HeadObject (`ObjectLockMode`, `ObjectLockRetainUntilDate`) and stub reads with `botocore.stub.Stubber` on a manually registered server's `clients.s3()`.

## Dev setup (once ticket 0 lands)
- `uv sync`
- `uv run ruff check . && uv run ruff format --check . && uv run pytest -q`: the same as CI and dmx's `test_command`.
- Live: `WASABI_LIVE=1 WASABI_MCP_READ_ONLY=false uv run pytest -q -m live`, with Wasabi keys set.

## Constraints
- Wasabi endpoints: `https://s3.<region>.wasabisys.com` (path-style), `https://iam.wasabisys.com`, `https://sts.wasabisys.com`, `https://stats.wasabisys.com` (not `api.stats...`), and `https://api.wacm.wasabisys.com/api`.
- Build only from public documentation. Never copy code from Wasabi's official MCP server or any third-party Wasabi MCP project.
- Never log or return the configured credentials.
- The factory runner can't write `.github/` (it's mounted as /dev/null). Changes to `.github/workflows/ci.yml` go to a maintainer: put the exact YAML in the job folder and the PR description.

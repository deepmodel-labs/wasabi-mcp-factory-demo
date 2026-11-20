# Tech context

## Stack (decided, per docs/prd.md section 4)
- Python 3.12+, managed with uv. Commit `uv.lock`; CI runs `uv sync --locked`.
- MCP Python SDK `mcp` 2.x. The server class is `mcp.server.mcpserver.MCPServer`; FastMCP is the 1.x name and doesn't exist in 2.x. Tool annotations use snake_case fields (`read_only_hint`, `destructive_hint`, `open_world_hint`). `call_tool` returns a `CallToolResult` with `structured_content`, `content` and `is_error`.
- boto3/botocore for S3, IAM and STS; httpx for Wasabi's REST APIs and S3/IAM extensions (signed with `botocore.auth.SigV4Auth`).
- Dev: pytest with pytest-asyncio (`asyncio_mode = auto`), moto with server mode, respx, ruff (line length 100).
- mcp 2.3.0 error handling: a tool exception reaches the client with its message only if it subclasses `mcp.server.mcpserver.exceptions.ToolError`, so `ToolRefused` does. In-process `MCPServer.call_tool` raises `ToolError` (`"Error executing tool <name>: <msg>"`) rather than returning `is_error`; `ToolAnnotations` is in `mcp.types`.
- mcp 2.3.0 stdio client: `mcp.client.stdio.stdio_client(StdioServerParameters(...))` with `mcp.client.session.ClientSession`. `ThreadedMotoServer(port=0).get_host_and_port()` gives the bound port.
- respx matches routes in registration order: register path-specific routes before broad `host=` routes.

## Dev setup (once ticket 0 lands)
- `uv sync`
- `uv run ruff check . && uv run ruff format --check . && uv run pytest -q`: the same as CI and dmx's `test_command`.
- Live: `WASABI_LIVE=1 WASABI_MCP_READ_ONLY=false uv run pytest -q -m live`, with Wasabi keys set.

## Constraints
- Wasabi endpoints: `https://s3.<region>.wasabisys.com` (path-style), `https://iam.wasabisys.com`, `https://sts.wasabisys.com`, `https://stats.wasabisys.com` (not `api.stats...`), and `https://api.wacm.wasabisys.com/api`.
- Build only from public documentation. Never copy code from Wasabi's official MCP server or any third-party Wasabi MCP project.
- Never log or return the configured credentials.

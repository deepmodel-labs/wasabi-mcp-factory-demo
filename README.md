# Wasabi MCP server (DeepModel factory demo)

> [!NOTE]
> This project is a demonstration of the **DeepModel software factory**. The factory built it from Wasabi's public documentation, starting from the product requirements in [docs/prd.md](docs/prd.md).
>
> It is **not Wasabi's official MCP server**, and Wasabi doesn't maintain or support it. For Wasabi's official MCP server, hosted or self-hosted, see [Wasabi MCP for AI](https://wasabi.com/innovation-labs/mcp-for-ai) and [Wasabi's MCP documentation](https://docs.wasabi.com/docs/hosted-wasabi-mcp-with-ai-systems).

An [MCP](https://modelcontextprotocol.io) server that gives AI agents tools for a Wasabi cloud storage account:

- **Storage:** buckets, objects, versioning, object lock, lifecycle, policies, CORS, tags and presigned URLs.
- **Usage:** storage and bandwidth utilisation, per account and per bucket.
- **Identity:** IAM users, groups, roles, policies and access keys.
- **Account governance:** control, channel and sub-accounts, members, invoices and usage, through WACM Connect.

The full tool catalogue is in [docs/prd.md](docs/prd.md). Status: in progress. Tool groups land as the factory completes their [issues](../../issues).

## Quick start

You'll need Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/deepmodel-labs/wasabi-mcp-factory-demo
cd wasabi-mcp-factory-demo
uv sync
WASABI_ACCESS_KEY_ID=… WASABI_SECRET_ACCESS_KEY=… uv run wasabi-mcp
```

To use it from an MCP client such as Claude Desktop or Claude Code, add it as a stdio server:

```json
{
  "mcpServers": {
    "wasabi": {
      "command": "uv",
      "args": ["--directory", "/path/to/wasabi-mcp-factory-demo", "run", "wasabi-mcp"],
      "env": {
        "WASABI_ACCESS_KEY_ID": "…",
        "WASABI_SECRET_ACCESS_KEY": "…",
        "WASABI_REGION": "us-east-1"
      }
    }
  }
}
```

`uv run wasabi-mcp --transport streamable-http` serves MCP over HTTP instead.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `WASABI_ACCESS_KEY_ID`, `WASABI_SECRET_ACCESS_KEY` | (none) | Your Wasabi access key pair. |
| `WASABI_REGION` | `us-east-1` | The default region for storage calls. |
| `WASABI_MCP_READ_ONLY` | `true` | Write tools refuse to run until you set this to `false`. |
| `WASABI_WACM_USERNAME`, `WASABI_WACM_API_KEY` | (none) | WACM Connect credentials. Needed only for the account governance tools. |
| `WASABI_S3_ENDPOINT`, `WASABI_IAM_ENDPOINT`, `WASABI_STS_ENDPOINT`, `WASABI_STATS_ENDPOINT`, `WASABI_WACM_ENDPOINT` | Wasabi's public endpoints | Overrides, for private endpoints and tests. |

## Safety

- **The server starts read-only.** Every tool is marked read or write in its MCP annotations, and write tools refuse while `WASABI_MCP_READ_ONLY` is on.
- **Destructive tools also need `confirm=true` in the call.** These are deletes and changes to policies or access.
- **Credentials come only from the environment.** The server never logs or returns your configured credentials. Tools that create new keys (IAM access keys, sub-accounts) return the new secret once, and only when called with `confirm=true`.

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest -q          # against a local stand-in for Wasabi's APIs (moto)
WASABI_LIVE=1 WASABI_MCP_READ_ONLY=false uv run pytest -q -m live   # against real Wasabi; use a throwaway account
```

Each tool group is one module in `src/wasabi_mcp/tools/`. The conventions every tool follows are at the top of `src/wasabi_mcp/tools/__init__.py`.

## Licence

[Apache 2.0](LICENSE). Wasabi is a trademark of Wasabi Technologies, LLC. It's used here only to say which service this software works with.

---
ticket: gh-1
branch: feature-gh-1-foundation-project-config-safety-guards
summary: Foundation: project, config, safety guards, request helpers, test harness and CI (PRD ticket 0)
ticketing: github-issues
---

# Foundation: project, config, safety guards, request helpers, test harness and CI (PRD ticket 0)

**Ticket:** [#1](https://github.com/deepmodel-labs/wasabi-mcp-factory-demo/issues/1)
**Type:** feature
**Branch:** feature-gh-1-foundation-project-config-safety-guards

---

## Context
The repository holds only the PRD, README, licence and dmx setup. Ticket 0 (`docs/prd.md` section 4) builds the shared foundation that tickets 1–23 then extend in parallel by adding only their own tool modules, tests and docs. Because those tickets must never touch shared files, the foundation has to get the extension points (group discovery, fixtures, helpers) right the first time.

## Scope
- **Project (4.1):** `pyproject.toml` (Python >=3.12, hatchling build, `src/` layout), committed `uv.lock`; deps `mcp>=2,<3`, `boto3`, `httpx`; dev group `pytest`, `pytest-asyncio`, `moto[server]`, `respx`, `ruff`. Console script `wasabi-mcp = wasabi_mcp.server:main` plus `src/wasabi_mcp/__main__.py`; `--transport stdio|streamable-http` (default stdio). pytest config: `asyncio_mode = "auto"`, `testpaths = ["tests"]`, marker `live` registered, `addopts = "-m 'not live'"`. ruff `line-length = 100`, target py312.
- **`config.py`:** frozen `Settings` dataclass, `Settings.from_env()` for every variable in PRD section 7 (`WASABI_MCP_READ_ONLY` defaults to true; parse true/false/1/0/yes/no). Secret fields (`secret_access_key`, `wacm_api_key`, and the access key id) use `field(repr=False)`. `s3_endpoint_for(region)`, `iam_url`, `sts_url`, `stats_url`, `wacm_url` (overrides win), `has_keys`, `has_wacm`.
- **`clients.py`:** `Clients(settings)`, cached `s3(region=None)` (path-style addressing, region endpoint), `iam()`, `sts()` (signed for `us-east-1`), botocore `Config(retries={"mode": "standard"})`.
- **`safety.py`:** `ToolRefused`, `require_writable(settings, action)` (message says how to enable writes: `WASABI_MCP_READ_ONLY=false`), `require_confirmed(settings, action, confirm)`.
- **`errors.py`:** `explain(exc, action) -> ToolRefused` for `ClientError` (`"<action> failed: <Code>: <Message>"`) and `BotoCoreError`.
- **`rest.py`:** `signed_s3`, `iam_query`, `stats_get`, `wacm_request`, `parse_xml`, as specified in PRD 4.2, each raising `ToolRefused` on non-2xx or missing credentials.
- **`tools/__init__.py`:** `READ`, `WRITE`, `DESTRUCTIVE` annotations, `ToolContext`, `discover_groups()` (via `pkgutil.iter_modules`, `meta` first, rest alphabetical), `clean()`.
- **`tools/meta.py`:** `server_info` (R), the reference group module.
- **`server.py`:** `build_server(settings=None) -> MCPServer` with read-only-default instructions; registers every discovered group; `main()` parses `--transport` and runs.
- **Tests (4.3):** `tests/conftest.py` (`moto_url`, `settings`, `read_only_settings`, `server`, `read_only_server`, `FAKE`, `rest_settings`, `rest_server`, `live_settings`), `tests/helpers.py` (`call`, `ToolError`), empty `tests/__init__.py` and `tests/tools/__init__.py`, and foundation tests: `test_config.py`, `test_safety.py`, `test_errors.py`, `test_clients.py`, `test_rest.py`, `test_server.py` (server_info, annotations on every listed tool, no secret in output, discovery order).
- **CI (4.4):** `.github/workflows/ci.yml` with `test` and `live` jobs, `permissions: contents: read`, using `astral-sh/setup-uv`.
- **README:** a short dev quick start (`uv sync`, the test command, the run command) only if the README has no such section yet.

## Out of scope
- Every tool group in PRD 6.1–6.14 (tickets 1–23) and ticket 24 (catalogue check, secret-leak test over all R tools, `tests/live/` suite, `docs/tools.md`).
- `docs/tools/meta.md`: section 5.7 applies to tickets 1–23; `server_info` is documented in its docstring and the README.
- MCP resources/prompts, HTTP auth, PyPI/Docker packaging (PRD section 11).
- The stray untracked files in the working tree (`.bashrc`, `.idea`, `.vscode`, `package.json`, `node_modules`, `scripts/` etc.) are container artefacts and must not be committed.

## Technical Approach
- **Server API (mcp 2.x, latest 2.3.0):** `from mcp.server.mcpserver import MCPServer`; tools added with `@server.tool(name=..., annotations=READ)`; `ToolAnnotations(read_only_hint=..., destructive_hint=..., open_world_hint=True)` from `mcp.types`. Verify exact names and `call_tool`/`list_tools` return shapes against the installed SDK before writing `helpers.call` (PRD says `CallToolResult` with `structured_content`, `content`, `is_error`).
- **Annotations:** `READ` = read_only True, destructive False; `WRITE` = read_only False, destructive False; `DESTRUCTIVE` = read_only False, destructive True; all `open_world_hint=True` (they talk to Wasabi). `server_info` is `READ`.
- **Group contract:** each `tools/<module>.py` exposes `register(server, ctx: ToolContext)`; `build_server` iterates `discover_groups()` and imports by name, so new groups need no shared edits. `server_info` reports loaded groups from that same list.
- **REST signing:** build an `httpx.Request`, sign with `botocore.auth.SigV4Auth` over an `AWSRequest` (service `s3`/`iam`, region), copy signed headers to httpx. Bare subresources: pass `query` as a raw string or a mapping where a `None` value renders as a bare key (`?compose`), keep the exact URL used for signing and sending identical. S3 errors: parse `<Error><Code>/<Message>`; IAM errors: `<ErrorResponse><Error>`.
- **Tests:** moto `ThreadedMotoServer(ip_address="127.0.0.1", port=0)`; reset with `urllib.request` POST to `/moto-api/reset` after each test that used the fixture (function fixture wrapping the session one). respx mocks only the `FAKE` hosts; moto traffic bypasses respx because the reset uses urllib and boto3 uses urllib3.
- **Stdio check:** a test (or manual check during verify) starts `wasabi-mcp` via the SDK's stdio client and lists tools, confirming `server_info`.

---

## Questions
<!-- Answered by the factory from docs/prd.md and the memory bank. -->

1. Which `mcp` version should be pinned, and how do tests reach the tools: in-process `server.call_tool` or a client session?
   Answer: `mcp>=2.3,<3` (2.3.0 is current on PyPI). `tests/helpers.call` uses in-process `server.call_tool` as PRD 4.3 says; one test in `test_server.py` additionally spawns `wasabi-mcp` over stdio with the SDK client to prove the "starts over stdio and lists `server_info`" done-when.

2. Does `server_info` return the access key id, and what exactly counts as a secret in `repr` and output?
   Answer: Secret key and WACM API key are secrets per PRD 5.2 and must never appear. The access key id isn't listed as a secret, but the safest reading of "never returns secrets" is to report only `has_keys` / `has_wacm` booleans and exclude the access key id and WACM username from `repr` too. `server_info` returns: `version`, `region`, `read_only`, `endpoints` (s3 for the default region, iam, sts, stats, wacm), `keys_configured`, `wacm_configured`, `groups`.

3. How should `signed_s3` represent bare subresources like `?inventory&id=x` while keeping the SigV4 canonical query correct?
   Answer: Accept `query` as a mapping where `None` means a bare key (`{"inventory": None, "id": "x"}` → `?inventory&id=x`). Build the URL string once, sign that exact URL with botocore (which canonicalises bare keys as `key=`), and send the same string with httpx so httpx doesn't re-encode it. Tests assert the sent URL and the presence of `Authorization: AWS4-HMAC-SHA256` for a `MOVE` request.

4. What should `wacm_request` and `stats_get` do when their credentials are absent?
   Answer: Raise `ToolRefused` before any network call: Stats → "Stats API needs WASABI_ACCESS_KEY_ID and WASABI_SECRET_ACCESS_KEY"; WACM → "WACM Connect isn't configured: set WASABI_WACM_USERNAME and WASABI_WACM_API_KEY". `signed_s3` and `iam_query` do the same for missing key pairs. Tests assert no respx route was called.

5. Should the CI live job run any tests now, given `tests/live/` is created by ticket 24?
   Answer: The job is added now as specified. With no live tests, `pytest -m live` exits 5 ("no tests collected"); the job treats exit 5 as success (`uv run pytest -q -m live || [ $? -eq 5 ]`) so it stays green until ticket 24 adds tests. It's gated with `if: github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository` and a step-level check that the `WASABI_ACCESS_KEY_ID` secret is non-empty (secrets can't be used in job-level `if`, so pass it via `env` and test it in a first step that sets an output).

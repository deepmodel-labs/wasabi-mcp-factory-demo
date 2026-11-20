# System patterns

There's no code yet. The architecture to build is in `docs/prd.md` section 4 (the foundation, ticket 0), and the rules every tool follows are in section 5. Summary:

- **Layout:** `src/wasabi_mcp/` contains `config.py`, `clients.py`, `safety.py`, `errors.py`, `rest.py` and `server.py`, plus `tools/`, with one module per tool group, found automatically, and `meta.py` first.
- **Tool groups:** each module exposes `register(server, ctx)` and adds tools with `@server.tool(name=..., annotations=READ|WRITE|DESTRUCTIVE)`. Module names are in PRD section 9.
- **Safety:** W tools call `require_writable` first; D tools call `require_confirmed` and take `confirm: bool = False`. Guards run before any network call.
- **Results:** plain JSON via `clean()`. Lists use `{"items", "next_token", "truncated"}`; page-number APIs (Stats) use `items` and `truncated` plus `page_num`, `page_size`, `next_page_num`, `page_count`, `record_count`. Optional configuration that's absent returns `{"configured": false}`.
- **Errors:** `explain(exc, action)` for botocore; `rest.py` helpers raise `ToolRefused` themselves.
- **Tests:** moto server fixtures (`settings`, `server`, `read_only_server`) for S3, IAM and STS; respx with `rest_settings` / `rest_server` (fake hosts in `conftest.FAKE`) for Wasabi extensions, Stats and WACM. `tests.helpers.call` calls tools as a client would. Live tests are marked `live`, go in `tests/live/`, and clean up after themselves.
- **Reference group:** `tools/meta.py` (`server_info`) is the example to copy for a new group module. `ToolRefused` subclasses the SDK's `ToolError`, so its message is what the client sees.
- **Secret checks in tests:** `tests/conftest.py` uses recognisable fake credentials and exposes `SECRETS`, so tests can assert that no secret appears in a tool's output.
- **Parallel tickets:** tickets 1–23 add only their own module(s), test file(s) and `docs/tools/<module>.md`. They never edit shared files.
- **Bucket region resolution:** every module that changes buckets keeps its own private helper (`_bucket_client` / `_bucket_region`). An explicit `region` skips the lookup. Otherwise it calls GetBucketLocation, treats an empty constraint as `us-east-1`, and caches the answer per `register`. Read tools use the settings region, because GETs work on any regional endpoint. The copies exist so tickets don't edit shared files; a later ticket may consolidate them.
- **Tool error text in tests:** the MCP SDK prefixes tool errors with `Error executing tool <name>: `, so tests that check an exact message use `endswith` (or `match`).
- **Region lookup in respx tests:** respx only patches httpx, so boto3's GetBucketLocation can't be mocked with it. Create the bucket in a region on moto, and mock the moto host with respx for the `signed_s3` call.
- **Wasabi MOVE:** the `Destination` header is the bare destination key or prefix (as in Wasabi's object operations docs), not a URL. Compose bodies are `<ComposeRequest><Component><Key>…</Key></Component>…</ComposeRequest>`.
- **Wasabi compliance:** `?compliance` bodies are `<BucketComplianceConfiguration>` / `<ObjectComplianceConfiguration>`. The set tools send only the fields given and normalise times to UTC `YYYY-MM-DDTHH:MM:SSZ`. Retention can only be extended, so the current value is read before the PUT.

---
ticket: gh-34
branch: bug-gh-34-report-operation-host-and-reason-for
summary: Report operation, host and reason for REST transport errors in rest.py
ticketing: github-issues
---

# Report operation, host and reason for REST transport errors in rest.py

**Ticket:** [#34](https://github.com/deepmodel-labs/wasabi-mcp-factory-demo/issues/34)
**Type:** bug
**Branch:** bug-gh-34-report-operation-host-and-reason-for

---

## Context
Live testing on Wasabi (WMCP-B3, item WMCP-13) found that network failures in the REST-path tools reach the MCP client as a bare `Error executing tool <name>`. The `rest.py` helpers (`signed_s3`, `iam_query`, `stats_get`, `wacm_request`) only convert non-2xx HTTP responses into `ToolRefused`. httpx transport exceptions (`ConnectError`, `ConnectTimeout`, `ReadTimeout`, …) propagate as plain exceptions, and mcp 2.x hides the message of anything that isn't a `ToolError`.

Observed: DNS failure, connection refused/reset, or timeout → `Error executing tool <name>` with no reason.
Expected: `ToolRefused` naming the operation, the target host and the reason.

## Scope
- `src/wasabi_mcp/rest.py`: wrap the httpx network call in `_send` (used by `signed_s3` and `iam_query`), `stats_get` and `wacm_request`. Catch `httpx.TransportError` and raise `ToolRefused` from it.
- A small private helper (for example `_transport_error(operation, url, exc) -> ToolRefused`) that classifies the reason:
  - name resolution / DNS failure (a `ConnectError` whose cause or message is a `socket.gaierror` / "Name or service not known" / "nodename nor servname" / "getaddrinfo") → "couldn't resolve host"
  - `ConnectError` otherwise (refused, reset, unreachable) → "couldn't connect": refused / reset where detectable
  - `TimeoutException` subclasses → "timed out" (connect / read / write / pool)
  - `RemoteProtocolError` / `ReadError` / `WriteError` (connection reset mid-request) → "connection was reset or closed"
  - other `TransportError` → generic "network error"
- The operation names match the existing HTTP-error prefixes: `action` for `signed_s3`, `action_name` for `iam_query`, `Stats API GET <path>`, `WACM Connect <METHOD> <path>`. `_send` therefore needs the operation passed in.
- Only the host (`httpx.URL(url).host`) appears in the message: no path query string, headers or `str(exc)` that could echo the request.
- respx tests in `tests/test_rest.py` (helper-level) and at least one tool-level test through `rest_server` with `tests.helpers.call`, covering connect refused, DNS failure and timeout, and asserting nothing from `conftest.SECRETS` (or the access key / a `Signature=` / `Authorization`) appears.
- Update `.dmx/systemPatterns.md` / `techContext.md` with a one-line note on transport-error handling.

## Out of Scope
- boto3 (urllib3) paths: `errors.explain` already covers botocore `EndpointConnectionError` etc. and respx can't intercept them.
- Retries on transport errors (including `_wacm.call`'s 429 retry, which stays as it is).
- Changing `TIMEOUT` or any HTTP 4xx/5xx message.
- Live tests.

## Technical Approach
- Layer: `rest.py` only, the shared REST helper. Every REST-path tool inherits the fix without edits to tool modules. This is a bugfix ticket, so editing the shared file is expected (the "tickets never edit shared files" rule applies to parallel tool-group tickets 1–23).
- `ToolRefused` subclasses the SDK `ToolError`, so its message reaches the client (techContext: mcp 2.3.0 error handling). Raise with `from exc` so the chain is kept for server-side debugging, but build the message only from the operation, the host and the classified reason.
- Message shape: `{operation} failed: {reason} {host}`, for example `PutObject failed: couldn't connect to s3.us-east-1.wasabisys.com (connection refused)`, `Stats API GET /v1/standalone/utilizations failed: couldn't resolve host stats.wasabisys.com`, `GetUser failed: timed out connecting to iam.wasabisys.com`. Keep the `"{op} failed: "` prefix that existing HTTP errors use.
- DNS detection: httpcore wraps `socket.gaierror` in `httpx.ConnectError`. Inspect `exc.__cause__`/`__context__` chain for `socket.gaierror`, and fall back to message substrings. Tests can raise `httpx.ConnectError("[Errno -2] Name or service not known")` and/or one with `__cause__ = socket.gaierror(...)`.
- Tests: respx `route.mock(side_effect=httpx.ConnectError("[Errno 111] Connection refused"))`, `side_effect=httpx.ConnectTimeout("timed out")`, `httpx.ReadTimeout(...)`. Matching uses `pytest.raises(match=...)` without `^`, because of the SDK prefix. Also confirm an existing 4xx test still produces the same message (no change).
- Watch the techContext note on a catch-all `except` around helpers that raise `ToolRefused` themselves. Catch only `httpx.TransportError`, so `ToolRefused` from signing/region lookup passes through.

---

## Questions
<!-- Answer each question before running /dmx/plan. -->

1. Should the message include the reason class only ("connection refused"), or also the underlying OS error text (for example `[Errno 111]`)?
   Answer: Classified reason only, with an optional short errno-derived word (refused/reset/unreachable). Never `str(exc)` wholesale, because httpx messages can include the request URL and its signed query (presigned-style `X-Amz-Signature`).

2. Should `ToolRefused.code` be set for transport errors so tools can branch on it, as they do with `NoSuchKey`?
   Answer: Yes, a stable code such as `NetworkError` / `DNSError` / `Timeout` costs nothing and lets `_wacm.call` and others branch without matching text. No tool needs it today.

3. Do any tool modules already wrap `rest.*` calls in a broad `except Exception` that would swallow or re-word the new `ToolRefused`?
   Answer: To verify during planning with a grep over `src/wasabi_mcp/tools/` for `except Exception` near `rest.` calls. Any that exist must let `ToolRefused` through unchanged.

4. Is `object_append`'s create-on-missing fallback (gh-30), which branches on `ToolRefused.code == "NoSuchKey"`, affected?
   Answer: No. Transport errors get a different code (or none), so the fallback doesn't trigger on them.

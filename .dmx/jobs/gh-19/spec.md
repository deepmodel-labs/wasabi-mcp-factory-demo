---
ticket: gh-19
branch: feature-gh-19-secure-remote-http-with-opt-in-bearer
summary: Secure remote HTTP with opt-in, bearer token and allowed hosts
ticketing: github-issues
---

# Secure remote HTTP with opt-in, bearer token and allowed hosts

**Ticket:** [#19](https://github.com/deepmodel-labs/wasabi-mcp-factory-demo/issues/19)
**Type:** feature
**Branch:** feature-gh-19-secure-remote-http-with-opt-in-bearer

---

## Context
A security review (WMCP-14, issue #19) found that `wasabi-mcp --transport streamable-http --host 0.0.0.0`
(or any non-loopback address) serves full control of the configured Wasabi account to the network with
no authentication. The MCP SDK (mcp 2.3.0) turns on DNS-rebinding protection only when the host is
`127.0.0.1`, `localhost` or `::1`, and the server prints no warning.

This job is **T1 of 5** in WMCP-B4 (item 1 of the issue only). Items 2–5 are separate tickets:
T2 read-only presign cap, T3 `WASABI_MCP_SECRETS_DIR` (already shipped in #27/#29), T4 unexpected
exceptions at the tool boundary, T5 the GitHub Actions SHA-pinning hand-off.

## Scope
- `src/wasabi_mcp/server.py` (`main()` holds the CLI; `__main__.py` only calls it):
  - New flags: `--allow-remote` (store_true) and a repeatable `--allowed-host` (`action="append"`).
  - New env vars: `WASABI_MCP_HTTP_TOKEN` (bearer token) and `WASABI_MCP_ALLOWED_HOSTS`
    (comma-separated; whitespace trimmed, empty entries ignored).
  - Startup check for `--transport streamable-http` on a non-loopback `--host`: fail fast (exit via
    `parser.error`, non-zero, no listener opened, `build_server()` not even needed) with a message
    naming the missing piece: (a) no `--allow-remote` → names `--allow-remote`; (b) `--allow-remote`
    but `WASABI_MCP_HTTP_TOKEN` unset/empty/whitespace → names `WASABI_MCP_HTTP_TOKEN`.
  - Loopback hosts (`127.0.0.1`, `localhost`, `::1`, and any `127.0.0.0/8` or `::1` address, checked
    with `ipaddress`) behave as today: no flag, no token needed.
  - DNS-rebinding protection always on for streamable-http, via an explicit
    `TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=..., allowed_origins=...)`.
- Bearer-token ASGI middleware wrapping the whole Starlette app (every route, including any 404s):
  - Applied only when remote is allowed (token configured).
  - `Authorization: Bearer <token>` required; missing, malformed or wrong → HTTP 401 with
    `WWW-Authenticate: Bearer` and a short body. Compared with `hmac.compare_digest` on bytes.
  - Non-`http` scopes (`lifespan`) pass through untouched.
  - The token and the `Authorization` header are never logged or echoed.
- Run path: build the app with `server.streamable_http_app(transport_security=..., host=...)`, wrap
  it in the middleware, and serve it with `uvicorn` (already an SDK dependency) at `--host/--port`,
  instead of `server.run("streamable-http", ...)`. Put the app-building in a testable function
  (e.g. `http_app(server, host, token, allowed_hosts)`) so tests use Starlette's `TestClient`
  without opening a socket.
- Tests (`tests/test_http_security.py`, new):
  - Non-loopback host refused without `--allow-remote`; refused with it but token missing or empty;
    both messages name the missing piece; uvicorn/`build_server` never called (monkeypatched to fail).
  - Remote allowed + token: no token, wrong token, wrong scheme → 401 on `/mcp` and on an
    unknown path; correct token → request reaches the MCP app (not 401/421).
  - Default `127.0.0.1`: no token needed; request served.
  - Host header not in the allowed list → 421; defaults accept bound host and loopback; hosts from
    repeated `--allowed-host` and from `WASABI_MCP_ALLOWED_HOSTS` accepted.
  - Token never appears in captured logs/output.
- Docs:
  - README: a "Remote HTTP" section (or under the existing streamable-http line, ~line 46) covering
    `--allow-remote`, `WASABI_MCP_HTTP_TOKEN`, `--allowed-host`, `WASABI_MCP_ALLOWED_HOSTS` and their
    defaults, a reverse-proxy example, and a **breaking change** note for anyone already running on
    a non-loopback host. Add the two env vars to the README Configuration table.
  - `docs/prd.md` section 7: add the two env vars; bump the pinned count in `tests/test_docs.py`
    from 12 to 14 (that test checks README vs PRD section 7 variables).
  - Memory bank (`.dmx/systemPatterns.md`) note on the HTTP security pattern, as part of the job.

## Out of Scope
- SSE transport (the CLI only offers `stdio` and `streamable-http`).
- TLS termination (left to a reverse proxy; documented in the example).
- OAuth / the SDK's `auth` / `token_verifier` machinery: a static bearer token is what the ticket asks.
- Items 2–5 of issue #19 (T2–T5), and transport error text from #17 item 5.
- Rate limiting, multiple tokens, token rotation without restart.

## Technical Approach
- Allowed hosts = union of defaults (bound host + `127.0.0.1`, `localhost`, `::1`), every
  `--allowed-host`, and every `WASABI_MCP_ALLOWED_HOSTS` entry, de-duplicated with order kept.
- SDK form (from `mcp/server/transport_security.py`): the Host header is matched exactly against an
  entry, or an entry ending in `:*` matches `base:<any port>`. So each entry is normalised to:
  - entry with an explicit port (`host:port`, `[v6]:port`) → kept as given;
  - entry without a port → both `host` (proxies on 80/443 send no port) and `host:*`;
  - bare IPv6 (`::1`, bound v6 address) → bracketed: `[::1]`, `[::1]:*`.
- Allowed origins derived from the same hosts: `http://` and `https://` for each entry (with the
  same `:*` handling). Non-browser MCP clients send no `Origin` and pass the SDK's check.
- Middleware order: bearer-token middleware is the outermost layer, so unauthenticated requests are
  rejected before Host/Origin validation and before the SDK's session manager.
- `0.0.0.0`/`::` as the bound host are added to the list per the defaults, but no client sends them
  as Host; the README tells remote operators to add the real hostname with `--allowed-host`.
- Settings stay in `server.py` (CLI concerns), not `config.Settings`, since they only affect the
  HTTP transport; `server_info` is not changed (never reports the token).

---

## Questions

1. Should loopback-but-not-literal addresses (e.g. `127.0.0.2`) count as loopback?
   Answer: Yes. Use `ipaddress.ip_address(host).is_loopback` for IP literals, plus the name
   `localhost`. Anything else (hostnames, `0.0.0.0`, `::`, LAN IPs) is non-loopback.

2. Should a token set on a loopback host also be enforced?
   Answer: No — the ticket says loopback keeps working as today with no token. The middleware is
   installed only when remote is allowed. (`--allow-remote` on a loopback host is accepted and
   harmless; the token isn't required there.)

3. What form do `--allowed-host` / `WASABI_MCP_ALLOWED_HOSTS` entries take?
   Answer: `host` or `host:port` as the client sends in `Host`; a bare host also allows any port
   (see Technical Approach). `:*` wildcard entries given by the operator are passed through.

4. What does a refused start look like?
   Answer: `argparse` `parser.error(...)` → exit code 2 with `wasabi-mcp: error: <message>` on
   stderr, before `build_server()` or uvicorn. Example: "--host 0.0.0.0 is not a loopback address:
   pass --allow-remote and set WASABI_MCP_HTTP_TOKEN to serve over the network".

5. How do the new env vars interact with `tests/test_docs.py`'s pinned variable count?
   Answer: add both to the PRD section 7 table and the README table, and change the count 12 → 14.

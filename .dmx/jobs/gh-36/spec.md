---
ticket: gh-36
branch: bug-gh-36-hide-unexpected-exception-text-from
summary: Hide unexpected exception text from clients at the server.py tool boundary
ticketing: github-issues
---

# Hide unexpected exception text from clients at the server.py tool boundary

**Ticket:** [#36](https://github.com/deepmodel-labs/wasabi-mcp-factory-demo/issues/36)
**Type:** bug
**Branch:** bug-gh-36-hide-unexpected-exception-text-from

---

## Context
Item 4 of the #17 security review (WMCP-17, T4 of 5 in WMCP-B4). In mcp 2.3.0 a tool exception
that isn't a `ToolError` reaches the client as `Error executing tool <name>: <str(exc)>`, so internal
exception text (which can echo a signed URL, a key or a token) goes into the model's context, and
nothing is logged. (Planning check against the installed mcp 2.3.0: `Tool.run` already wraps a crash
as `UnexpectedToolError("Error executing tool <name>")` without the text, but `_handle_call_tool`
then logs the traceback through `logger.exception` with no redaction, so secrets in the exception
text or traceback reach stderr. The client message also names only the tool, not that it was an
internal error. Our boundary wrapper must catch the crash *before* the SDK does, so that only our
redacted log is written.) The blocker, WMCP-13 (gh-34, REST transport errors), has merged on `main`
(`2b39a46`): `rest._transport_error` already turns httpx transport errors into
`"{operation} failed: {reason}"` `ToolRefused`s, and `errors.explain` formats botocore errors.

Observed: an unexpected `AttributeError` in a tool returns its class/text to the client.
Expected: a short message naming the failed tool, with the full details and traceback logged to
stderr, secrets removed.

## Scope
- `src/wasabi_mcp/errors.py`: extend the existing helper module (next to `explain`) with the
  client-facing message for an unexpected exception and the redacted stderr logging. No second
  error-formatting helper anywhere.
- `src/wasabi_mcp/server.py` `build_server()`: apply it once, centrally, around every registered
  tool (all groups are registered there), so no tool module changes.
- Pass through unchanged: `ToolRefused` / any `ToolError` (validation, read-only refusal,
  `confirm=true`, `explain()`'s ClientError messages, gh-34 transport errors).
- Redaction: the configured access key id and secret key, plus session tokens / secret values
  appearing in exception text or traceback, are removed from the stderr log.
- Tests (new `tests/test_errors_boundary.py` or extend existing error tests) and a short note in
  `.dmx/systemPatterns.md` / docs where error handling is described.

## Out of Scope
- Changing the wording of `explain()` ClientError messages or gh-34 transport errors.
- Tightening `explain()`'s `BotoCoreError` branch (it still includes `str(exc)`) — see Question 3.
- The other WMCP-B4 items (HTTP auth, presign in read-only, returned secrets, Actions pinning).
- Editing individual tool modules.

## Technical Approach
- In `build_server`, wrap tool registration: after groups register, wrap each tool's function in
  the server's tool manager (or wrap `server.tool` before calling `register`) with a decorator that
  preserves the signature (`functools.wraps`, sync and async), so the SDK still derives the same
  input schema. `tests/test_catalogue.py` / `list_tools` must be unchanged.
- In the wrapper: `except ToolError: raise`; `except Exception as exc:` → log via
  `logging.getLogger("wasabi_mcp")` (stderr handler; never stdout, which stdio transport uses) with
  the tool name and a redacted `traceback.format_exception(exc)`, then
  `raise unexpected(tool_name) from None` where `errors.unexpected` returns
  `ToolRefused(f"{tool} failed: unexpected internal error (see server log)")`.
- Redaction: replace the configured `Settings` access/secret keys with `[REDACTED]`, and regex-mask
  common secret shapes (`SecretAccessKey`/`SessionToken`/`X-Amz-Security-Token`/`X-Amz-Signature`
  / `Authorization` values). Use `tests/conftest.py` `SECRETS` in tests.
- Tests: register a throwaway tool on a `build_server`-built server (or monkeypatch a group) that
  raises `AttributeError("... <secret> ...")`; assert the client message names the tool and lacks
  `AttributeError` and the text; assert `caplog`/`capsys` stderr has the traceback and no secret;
  assert a `require_confirmed` refusal, a ClientError via `explain`, and a respx transport error are
  unchanged.

---

## Questions
<!-- Answer each question before running /dmx/plan. -->

1. Where exactly should the wrap hook in: wrapping `server.tool` before each group's `register` call,
   or post-registration rewrapping of the tool manager's `Tool.fn`? The first keeps the SDK's schema
   derivation untouched; the second relies on mcp 2.3.0 internals.
   Answer: Wrap at registration. `build_server` passes each group's `register` a thin proxy whose
   `.tool(...)` decorates the function with the boundary wrapper and then calls the real
   `server.tool(...)`. That uses only the public API. `functools.wraps` sets `__wrapped__`, and
   `func_metadata` uses `inspect.signature(eval_str=True)`, which follows `__wrapped__` and the
   original function's globals, so schemas don't change. Async tools get an `async def` wrapper so
   `is_async_callable` still holds. `test_catalogue.py` is the guard.

2. Should the logged message name the tool (`object_get`) or a PRD-style action (`GetObject`)? The
   boundary only knows the tool name, so the tool name is assumed.
   Answer: The tool name. The boundary has no other action name, and the acceptance criteria
   allow "tool or action".

3. `explain()` still renders `BotoCoreError` as `{Class}: {str(exc)}`, which can contain endpoint
   URLs. Leave as-is (user-facing per the item) or route it through the same redaction?
   Answer: Leave it as it is. The item says these messages must reach the client as they do today,
   and changing them is out of scope.

4. Logging setup: does the server configure a `logging` handler today, or should `server.main` add a
   stderr handler (level WARNING) so the traceback appears under both stdio and HTTP transports?
   Answer: No new handler. `MCPServer.__init__` calls the SDK's `configure_logging`, which does
   `logging.basicConfig` with a stderr `RichHandler` at INFO under both transports. Log through
   `logging.getLogger("wasabi_mcp")` (it propagates to root) at ERROR, with the redacted traceback
   already formatted into the message and *no* `exc_info`, so neither Rich nor any other handler
   re-renders the raw exception.

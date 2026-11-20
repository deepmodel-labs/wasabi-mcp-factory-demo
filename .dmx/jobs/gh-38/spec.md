---
ticket: gh-38
branch: feature-gh-38-cap-presignget-expiry-at-3600-seconds-in
summary: Cap presign_get expiry at 3600 seconds in read-only mode
ticketing: github-issues
---

# Cap presign_get expiry at 3600 seconds in read-only mode

**Ticket:** [#38](https://github.com/deepmodel-labs/wasabi-mcp-factory-demo/issues/38)
**Type:** feature
**Branch:** feature-gh-38-cap-presignget-expiry-at-3600-seconds-in

---

## Context
A security review of main (f78eca9) found that a read-only server can still create presigned download links valid for up to 7 days (604,800 s). Anyone holding such a link can fetch the data outside the account's access controls, long after the agent session ends. Read-only mode should limit this exposure. This is T2 of 5 in WMCP-B4 (security hardening); the tracker item is WMCP-15.

## Scope
- `src/wasabi_mcp/tools/transfer.py`. The item guessed `tools/s3_presign.py`, but `presign_get` and `presign_get_for_prefix` actually live in `transfer.py`. When `settings.read_only` is true, both tools clamp `expires_seconds` to 3,600 (clamped, never rejected) after `_check_expires` validates the 60–604,800 range.
- Sign each URL with the effective value. Return the effective `expires_seconds` and `expires_at`, plus a short note that read-only mode capped the expiry. The note appears only when clamping actually happened.
- `presign_get_for_prefix`: the existing result carries `expires_seconds` / `expires_at` at the top level, and they cover every item's URL. Keep that shape and add the note at the top level.
- Outside read-only mode, the current limits stay unchanged.
- Docs: both tools' docstrings (their MCP descriptions), `docs/tools/transfer.md` (then regenerate `docs/tools.md` with `uv run python -m tests.docs_tools`) and the README **Safety** section.
- Tests in `tests/tools/test_transfer.py`.

## Out of Scope
- `presign_put` and `multipart_upload_presign`: these are W tools, which read-only mode already refuses.
- A configurable cap or a new setting. That would also mean updating the PRD section 7 count pinned in `tests/test_docs.py`.
- Changing the 60–604,800 validation. Out-of-range values are still refused in both modes.
- Other WMCP-B4 items (HTTP auth, secrets, error detail, Actions pinning).

## Technical Approach
- Add `READ_ONLY_MAX_EXPIRES = 3_600` and a small helper, for example `_effective_expires(settings, expires_seconds) -> tuple[int, str | None]`, which returns the effective value and a note (or `None`). It calls `_check_expires` first so invalid inputs are still refused.
- Each tool uses the effective value for `_presign` and `_expires_at`, and adds `"note"` only when the helper returned one. `presign_get_for_prefix` already passes through `clean()`, which drops `None`. `presign_get` builds the dict directly, so add the key conditionally.
- Read-only status comes from `ctx.settings.read_only` (already bound as `settings` in `register`). These tools stay R tools: no `require_writable`.
- Tests: use the `read_only_server` fixture for the capped cases and `server` for the no-regression case. Parse `X-Amz-Expires` from the URL query, and check that `expires_at` is about now + 3,600 s (with a tolerance). For the prefix tool, seed a couple of objects and check every item's URL.

---

## Questions
<!-- Answer each question before running /dmx/plan. -->

1. What key name and wording should the cap note use?
   Answer: `note`, a short sentence such as "Read-only mode capped expires_seconds at 3600 (requested 604800)." The repo already returns note-style strings (for example `_secrets.SAVED_NOTE`), and a single `note` key is the simplest form a client can read.

2. Should `presign_get_for_prefix` carry the cap fields per item or at the top level?
   Answer: at the top level, matching the existing shape. All URLs in one call share one expiry, so the top-level fields apply to every item.

3. Should the cap be configurable (an environment variable)?
   Answer: no. The item fixes it at 3,600. A new setting would touch the README/PRD section 7 tables and the pinned count in `tests/test_docs.py`, which is out of scope.

4. Does the cap apply when the value is exactly 3,600?
   Answer: no clamping happens and no note is added. Values ≤ 3,600 are used as requested.

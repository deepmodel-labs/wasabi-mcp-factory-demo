---
ticket: gh-6
branch: feature-gh-6-add-wacm-connect-tool-groups-accounts
summary: Add WACM Connect tool groups (accounts, channels, sub-accounts, billing)
ticketing: github-issues
---

# Add WACM Connect tool groups (accounts, channels, sub-accounts, billing)

**Ticket:** [#6](https://github.com/deepmodel-labs/wasabi-mcp-factory-demo/issues/6)
**Type:** feature
**Branch:** feature-gh-6-add-wacm-connect-tool-groups-accounts

Item WMCP-7 (T6 of 7 in WMCP-B2, parent #3).

---

## Context
The foundation (PRD section 4) is merged, but no WACM Connect tools exist, so an agent can't govern a Wasabi account hierarchy. Scenarios S6 ("make a new tenant: a sub-account with a 10 TB hard quota") and S7 ("which sub-accounts are near quota this month?") need them. This ticket adds the 32 tools of PRD section 6.14 (PRD tickets 20–23), all built on `rest.wacm_request` with HTTP Basic auth (`WASABI_WACM_USERNAME`, `WASABI_WACM_API_KEY`).

## Scope
- `src/wasabi_mcp/tools/wacm_accounts.py` (9): `wacm_standalone_account_list` R, `wacm_standalone_account_create` W, `wacm_storage_amount_list` R, `wacm_country_list` R, `wacm_control_account_list` R, `wacm_control_account_get` R, `wacm_control_account_usage_list` R, `wacm_control_account_usage_get` R, `wacm_control_account_bucket_list` R.
- `src/wasabi_mcp/tools/wacm_channels.py` (8): `wacm_channel_account_list` R, `_get` R, `_create` W, `_update` W, `_delete` D (say it unlinks sub-accounts); `wacm_channel_user_create` W, `_update` D, `_delete` D.
- `src/wasabi_mcp/tools/wacm_sub_accounts.py` (6): `wacm_sub_account_list` R, `_get` R, `_create` D (returns `access_key_id` / `secret_access_key` once), `_update` D (PATCH; active, trialToPaid, quota, resetKeys, resetMFA), `_delete` D, `wacm_sub_account_bucket_list` R.
- `src/wasabi_mcp/tools/wacm_members.py` (5): `wacm_member_list` R, `_get` R, `_create` D, `_update` D, `_delete` D.
- `src/wasabi_mcp/tools/wacm_billing.py` (4): `wacm_invoice_list` R, `wacm_invoice_get` R, `wacm_usage_list` R, `wacm_usage_get` R.
- List tools take `page` and `size`; filters per the catalogue, passed as camelCase query params (`None` dropped by `wacm_request`).
- HTTP 429: retry once after `Retry-After` (default 5 s), then report.
- Tests: `tests/tools/test_wacm_accounts.py`, `test_wacm_channels.py`, `test_wacm_sub_accounts.py`, `test_wacm_members.py`, `test_wacm_billing.py`, with respx against `FAKE["wacm"]` using `rest_settings` / `rest_server`.
- Docs: `docs/tools/wacm_accounts.md`, `wacm_channels.md`, `wacm_sub_accounts.md`, `wacm_members.md`, `wacm_billing.md`.

## Out of Scope
- Changes to shared foundation files (`rest.py`, `config.py`, `safety.py`, `server.py`, `conftest.py`, `helpers.py`): parallel-ticket rule.
- Live tests against real WACM (the PRD's live core flows are S3/IAM/Stats; WACM needs a partner control account). Can be added later if credentials exist.
- `includeKeys` / `includeApiKey` query flags — never sent.
- `docs/tools.md` assembly (PRD ticket 24).

## Technical Approach
- Copy the `tools/meta.py` pattern: each module exposes `register(server, ctx)` and adds tools with `@server.tool(name=..., annotations=READ|WRITE|DESTRUCTIVE)`; modules are auto-discovered.
- W tools call `require_writable(settings, "<Action>")` first; D tools take `confirm: bool = False` and call `require_confirmed(...)` first. Guards run before any network call.
- `rest.wacm_request` already raises `ToolRefused` naming `WASABI_WACM_USERNAME and WASABI_WACM_API_KEY` when `has_wacm` is false, and formats `{error, code}` as `HTTP <status>: <error>`. Ordering: the WACM-configured check happens inside `wacm_request`, after the safety guards, so read-only/confirm refusals still win for W/D tools.
- 429 retry: a small private helper in a module (e.g. `wacm_sub_accounts.py` or a shared private `_wacm_call` duplicated per module, since shared files can't change) that catches the 429 `ToolRefused`… TODO: `wacm_request` does not expose status/headers; decide in planning (see Q1).
- Results pass through `clean()`. For lists, return WACM's page envelope normalised to `{"items", "next_token", "truncated"}` with `next_token` as the next page number string (see Q2).
- `wacm_sub_account_create` sends a body with `name`, `wasabiAccountEmail`, `password` or `sendWelcomeEmail`, `controlAccountId`/`channelAccountId`, `isTrial`, `trialQuotaTB`, `trialDays`, `purchasedStorageTB`, `storageQuotaType`; validates name length 3–100 and that `Hard` requires `purchasedStorageTB` before calling WACM.
- Tests assert Basic auth header, JSON bodies, read-only and confirm refusals with zero respx calls, the `has_wacm=false` message for every tool, and that `SECRETS` never appear in outputs.

---

## Questions
<!-- Answer each question before running /dmx/plan. -->

1. `rest.wacm_request` raises `ToolRefused` on any non-2xx and doesn't expose the status or `Retry-After` header, and shared files can't be edited. How should the "retry once on 429" rule be met?
   Answer: Add a private helper module `src/wasabi_mcp/tools/_wacm.py` (new file, owned by this ticket; leading underscore so tool discovery skips it, or no `register`) that wraps `wacm_request`: on a `ToolRefused` whose message contains `HTTP 429`, sleep 5 s (default; header isn't visible) and retry once. Tests patch the sleep. If discovery would import it, give it a no-op `register`.

2. How are WACM's paged list responses mapped to the `{"items", "next_token", "truncated"}` shape when WACM uses `page`/`size`?
   Answer: Pass `page`/`size` through; return `items` from WACM's data array, `truncated` true when more pages remain, and `next_token` as the next page number (string) or null. Keep WACM's own paging fields (e.g. total count) alongside.

3. Which secrets does `wacm_sub_account_create` return, and are other WACM responses (control accounts with API keys, sub-account keys) filtered?
   Answer: Only `wacm_sub_account_create` returns `access_key_id` and `secret_access_key` (D, so confirm=true). Other tools never send `includeKeys`/`includeApiKey`, and defensively drop any `apiKey`/`secretAccessKey`-like fields from responses.

4. For S7, should a tool compute "near quota", or just return the data?
   Answer: Just return the data: `wacm_sub_account_list` gives `purchasedStorageTB`/quota type, `wacm_usage_list` gives per-sub-account usage for the period; the respx test joins them to show the agent can compute usage vs quota.

5. Kinds that look surprising (`wacm_sub_account_update`, `wacm_channel_user_update`, `wacm_member_create/update` as D): follow the catalogue?
   Answer: Yes, use the catalogue kinds exactly. "Delete tools refuse without confirm=true" is a subset of D behaviour.

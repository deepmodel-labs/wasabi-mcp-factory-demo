---
ticket: gh-4
branch: feature-gh-4-add-iam-and-sts-tool-groups-users-keys
summary: Add IAM and STS tool groups (users, keys, groups, roles, policies, account)
ticketing: github-issues
---

# Add IAM and STS tool groups (users, keys, groups, roles, policies, account)

**Ticket:** [#4](https://github.com/deepmodel-labs/wasabi-mcp-factory-demo/issues/4)
**Type:** feature
**Branch:** feature-gh-4-add-iam-and-sts-tool-groups-users-keys

Item WMCP-5, T4 of 7 in WMCP-B2 (parent issue #3).

---

## Context
The foundation (PRD section 4) is on `main`, but no IAM or STS tools exist, so an agent can't manage identities on a Wasabi account. This ticket delivers PRD tickets 12–18 (catalogue sections 6.11 and 6.12): 60 tools across seven new tool-group modules, built on `Clients.iam()` / `Clients.sts()` and, for the two Wasabi-only actions, `rest.iam_query`. It enables scenarios S3 (onboard a read-only contractor) and S8 (remove a user's access everywhere).

## Scope
New files only (PRD section 9: parallel tickets never edit shared files):

- `src/wasabi_mcp/tools/iam_users.py` (9 tools): `iam_user_list`, `iam_user_get`, `iam_user_create`, `iam_user_update`, `iam_user_delete` (`cascade`), `iam_login_profile_get`, `iam_login_profile_create`, `iam_login_profile_update`, `iam_login_profile_delete`.
- `src/wasabi_mcp/tools/iam_keys.py` (5): `iam_access_key_list` (`all_users` → `UserName=*`), `iam_access_key_create`, `iam_access_key_update` (`active: bool`), `iam_access_key_delete`, `iam_access_key_last_used`.
- `src/wasabi_mcp/tools/iam_groups.py` (8): `iam_group_list`, `iam_group_get`, `iam_group_create`, `iam_group_update`, `iam_group_delete` (`cascade`), `iam_group_add_user`, `iam_group_remove_user`, `iam_user_group_list`.
- `src/wasabi_mcp/tools/iam_roles.py` (8): `iam_role_list`, `iam_role_get`, `iam_role_create`, `iam_role_delete` (`cascade`), `iam_role_trust_policy_update`, `sts_caller_identity`, `sts_assume_role`, `sts_session_token`.
- `src/wasabi_mcp/tools/iam_policies.py` (10): `iam_policy_list`, `iam_policy_get`, `iam_policy_create`, `iam_policy_delete` (`cascade`), `iam_policy_version_list`, `iam_policy_version_get`, `iam_policy_version_create`, `iam_policy_version_delete`, `iam_policy_version_set_default`, `iam_policy_entities_list`.
- `src/wasabi_mcp/tools/iam_policy_bindings.py` (8): `iam_policy_attach`, `iam_policy_detach`, `iam_attached_policy_list`, `iam_inline_policy_list`, `iam_inline_policy_get`, `iam_inline_policy_put`, `iam_inline_policy_delete`, `iam_policy_validate` (ValidatePolicy, `rest.iam_query`).
- `src/wasabi_mcp/tools/iam_account.py` (12): `iam_account_summary`, `iam_account_authorization_details`, `iam_password_policy_get`, `iam_password_policy_set`, `iam_password_policy_delete`, `iam_account_alias_list`, `iam_account_alias_create`, `iam_account_alias_delete`, `iam_mfa_device_list`, `iam_user_mfa_get` (GetUserMFA, `rest.iam_query`), `iam_mfa_virtual_device_create`, `iam_mfa_device_deactivate`.
- Optional private helper `src/wasabi_mcp/tools/_iam.py` (skipped by `discover_groups` because of the leading underscore) for logic shared by the seven modules.
- Tests: `tests/tools/test_<module>.py` for each module, plus an S3/S8 scenario test.
- Docs: `docs/tools/<module>.md` for each module, one table (name, kind, description) in catalogue order.

## Out of Scope
- Live tests against real Wasabi (ticket 24, `tests/live/`).
- `tests/catalogue.py`, the global secret-leak test and `docs/tools.md` (ticket 24). This ticket does assert no secret leaks in its own tools' outputs.
- Any edit to foundation files (`config.py`, `clients.py`, `safety.py`, `errors.py`, `rest.py`, `server.py`, `tools/__init__.py`, `tests/conftest.py`, `tests/helpers.py`, `pyproject.toml`, CI). If a foundation gap is found, work around it inside the new modules and note it in the PR.
- S3, Stats and WACM tools (other tickets in WMCP-B2).

## Technical Approach
- **Module shape:** copy `tools/meta.py`: `register(server, ctx)` with `@server.tool(name=..., annotations=READ|WRITE|DESTRUCTIVE)`; the docstring is the MCP description. `iam = ctx.clients.iam()` / `ctx.clients.sts()` is fetched inside each tool (clients are cached).
- **Kinds and guards (PRD 5.2):** kinds exactly as in the catalogue. W tools call `require_writable(settings, "<Action>")` first; D tools take `confirm: bool = False` and call `require_confirmed(...)` first, before any client call. Note `iam_group_add_user` and `iam_policy_attach` are D, so the S3 scenario passes `confirm=true` to them.
- **Outputs (PRD 5.4):** `clean()` every boto3 response; lists return `{"items", "next_token", "truncated"}` with `max_items` (default 100, cap 1,000) and `continuation_token` mapped to IAM's `MaxItems`/`Marker`/`IsTruncated`. Policy documents (managed version, inline, trust policy) are URL-decoded/parsed into JSON objects. Absent optional config (`NoSuchEntity` on login profile, password policy) returns `{"configured": false}`.
- **Inputs (PRD 5.3):** policy documents accepted as `dict` or JSON `str`, validated with `json.loads` before any call; invalid JSON raises `ToolRefused` without a network call. `principal_type` is a `Literal["user", "group", "role"]` dispatched to the matching `*_user_*`/`*_group_*`/`*_role_*` boto3 method (shared helper in `_iam.py`).
- **Errors (PRD 5.5):** `except (ClientError, BotoCoreError) as exc: raise explain(exc, "<Action>") from exc`. Add targeted hints: `DeleteConflict` without `cascade` (say what blocks the delete), `LimitExceeded` on `CreatePolicyVersion` (five versions; delete one), and `AccessDenied` from `AssumeRole` when called as root (Wasabi doesn't allow root to assume roles).
- **Cascade deletes:** users, groups, roles and policies enumerate dependents (keys, login profile, memberships, attached/inline policies, MFA devices, non-default versions, entities) and remove them, returning a `steps` list naming each action.
- **Secrets:** only `iam_access_key_create` (`access_key_id`, `secret_access_key`, plus a note it can't be retrieved again), `iam_login_profile_create` (`password`, generated with `secrets` when absent), `iam_mfa_virtual_device_create` (Base32 seed and base64 QR PNG) and `sts_assume_role` / `sts_session_token` (temporary `credentials`) return secrets, all D. `iam_access_key_list`, `iam_user_get` etc. return IDs, status and dates only. `sts_caller_identity` returns account, user ID and ARN, never keys.
- **Wasabi extensions:** `iam_policy_validate` → `rest.iam_query(settings, "ValidatePolicy", {...})` with `PolicyDocument`, `IsPrincipal`, optional `UseAction`/`UseResource`/`UseService`; `iam_user_mfa_get` → `rest.iam_query(settings, "GetUserMFA", {"UserName": ...})`. Convert the returned `ElementTree.Element` into JSON (strip namespaces, flatten `member` lists) in `_iam.py`.
- **Tests (PRD 5.6):** moto fixtures `server` / `read_only_server` for everything moto supports; respx with `rest_server` and `conftest.FAKE["iam"]`/`["sts"]` for ValidatePolicy, GetUserMFA and anything moto lacks or fakes wrongly (e.g. `UserName=*`). Each tool: happy path, read-only refusal (W/D), `confirm=false` refusal (D), and an error path where documented. respx tests assert the request is a POST with `application/x-www-form-urlencoded`, `Action=...`, `Version=2010-05-08` and an `Authorization: AWS4-HMAC-SHA256` header. Leak checks assert no value in `conftest.SECRETS` (and no `SecretAccessKey`) appears in any non-secret tool's output. Use `tests.helpers.call`.
- **Gate:** `uv run ruff check . && uv run ruff format --check . && uv run pytest -q`.

---

## Questions
<!-- Answered from the PRD, the memory bank and the foundation code (unattended run). -->

1. May the seven modules share code through a new private module, given parallel tickets must not edit shared files?
   Answer: Yes. Add `src/wasabi_mcp/tools/_iam.py`; it's a new file, and `discover_groups` skips modules starting with `_`, so it isn't registered as a tool group. Keep it limited to IAM helpers (principal dispatch, pagination, policy JSON, XML-to-JSON).

2. Where moto doesn't implement an action or Wasabi behaviour (e.g. `UserName=*` on ListAccessKeys, `GetAccountAuthorizationDetails` filters, virtual MFA QR output), how is it tested?
   Answer: Per PRD 5.6, don't skip it: mock the IAM endpoint with respx on `rest_server` (boto3 against `FAKE["iam"]`), returning Wasabi-shaped XML. Prefer moto wherever it works.

3. What does `sts_session_token`/`sts_assume_role` return, and how are secrets named?
   Answer: `{"credentials": {"access_key_id", "secret_access_key", "session_token", "expiration"}, ...}` plus the assumed-role user for AssumeRole, with a note that the secret is shown only once. Both are D and refuse without `confirm=true`; `duration_seconds` is validated (≤ 43,200) before any call.

4. Do MFA-device tools that return the virtual MFA seed/QR count as secret-returning for leak tests?
   Answer: Yes. Only `iam_mfa_virtual_device_create` (D, confirmed) returns `base32_seed` and `qr_png_base64`; `iam_mfa_device_list` and `iam_user_mfa_get` return serials and dates only.

5. Should `iam_policy_list` default to `scope="Local"` or `"All"`?
   Answer: `"All"`, matching the IAM API default and the catalogue listing All first; agents pass `Local` for customer-managed policies (S4).

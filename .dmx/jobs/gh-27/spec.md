---
ticket: gh-27
branch: feature-gh-27-add-optional-wasabi-mcp-secrets-dir-to
summary: Add optional WASABI_MCP_SECRETS_DIR to keep new secrets out of tool results
ticketing: github-issues
---

# Add optional WASABI_MCP_SECRETS_DIR to keep new secrets out of tool results

**Ticket:** [#27](https://github.com/deepmodel-labs/wasabi-mcp-factory-demo/issues/27)
**Type:** feature
**Branch:** feature-gh-27-add-optional-wasabi-mcp-secrets-dir-to

---

## Context
A security review of main (f78eca9) found that tools which create credentials return the new secrets straight into the model's context (WMCP-16, T3 of 5 in the WMCP-B4 security hardening batch). An optional `WASABI_MCP_SECRETS_DIR` lets operators keep new secrets on disk, out of the conversation. When unset, behaviour is unchanged.

## Scope
- `src/wasabi_mcp/config.py`: optional `secrets_dir` setting read from `WASABI_MCP_SECRETS_DIR` (unset or empty → `None`).
- New private helper `src/wasabi_mcp/tools/_secrets.py` (underscore, so `discover_groups` skips it):
  - `prepare(ctx)` / directory check, run before the Wasabi call: create the directory with mode 0700 if missing; refuse with `ToolRefused` if it is group- or world-accessible (`mode & 0o077`), with a message such as `chmod 700 <dir>`; refuse if not writable (`os.access(W_OK|X_OK)`) or not a directory.
  - `write(...)`: create a new file with `os.open(path, O_CREAT|O_EXCL|O_WRONLY, 0o600)` and a name from `secrets.token_hex` (e.g. `<tool>-<hex>.json`), write JSON, fsync, close. On failure after the API call, raise `ToolRefused` naming the created resource (access key ID, user name, MFA serial, sub-account ID, or "STS session for <role>") so the operator can delete or rotate it.
- The six tools in their real modules:
  - `iam_access_key_create` — `tools/iam_keys.py` (file: secret access key; result: access key ID, user name, status, create date, path)
  - `iam_login_profile_create` — `tools/iam_users.py` (file: password; result: user name, create date, reset-required flag, path)
  - `iam_mfa_virtual_device_create` — `tools/iam_account.py` (file: JSON with `base32_string_seed` and base64 `qr_code_png`; result: serial number and path only)
  - `sts_assume_role`, `sts_session_token` — `tools/iam_roles.py` (file: access key ID, secret access key, session token, expiration; result: access key ID, expiration, assumed role user, path)
  - `wacm_sub_account_create` — `tools/wacm_sub_accounts.py` (file: access key ID and secret key; result: scrubbed `sub_account`, access key ID, path)
- Tests for each tool in both modes, directory creation/refusal, uniqueness and no-overwrite.
- README: document `WASABI_MCP_SECRETS_DIR` and both modes; update `docs/tools/<module>.md` for the affected tools and regenerate `docs/tools.md`.

## Out of Scope
- Changing other tools that might expose secrets indirectly (e.g. `wacm_sub_account_update` with resetKeys, which already doesn't return keys).
- Cleanup/rotation of secret files, file encryption, or a tool that reads the files back.
- The other WMCP-B4 items (HTTP auth, presign in read-only, error detail, Actions pinning).
- `.github/` changes (the runner can't write there).

## Technical Approach
- Follows the private-helper pattern of `tools/_iam.py` / `tools/_wacm.py`: one shared module imported by the group modules.
- Order inside each tool: `require_writable` / `require_confirmed` guards → secrets-dir check (only when set) → Wasabi call → write file → return result. This guarantees the API is never called when the directory is unusable.
- Results go through `clean()` as now. Unset mode keeps the existing result exactly (regression tests compare against the current shape).
- Tests: moto fixtures for IAM/STS (MFA seed/PNG come back from moto), Stubber (`tests/tools/iam_support.py`) with no queued responses to prove a refused call never reached Wasabi, respx for WACM (`tests/tools/wacm_support.py`). Use `conftest.SECRETS` and the returned secret values to assert nothing secret appears in `structured_content` or `content`. Use `tmp_path` and `monkeypatch`/settings to set the directory; skip the not-writable test when running as root.
- `tests/test_docs.py` requires every PRD section 7 variable in the README; adding the variable to README is required, and the PRD variable list should be checked for whether it needs the new entry too.

---

## Questions
<!-- Answer each question before running /dmx/plan. -->

1. Should the secrets-dir check be a new function in `safety.py` (shared file) or live entirely in `tools/_secrets.py`?
   Answer: In `tools/_secrets.py`, keeping shared-file edits to `config.py` and README; it raises `ToolRefused` from `errors.py`.

2. How is a relative `WASABI_MCP_SECRETS_DIR` handled, and is `~` expanded?
   Answer: Expand `~` and resolve to an absolute path at settings load, so the returned file path is absolute and unambiguous.

3. Should the directory check follow symlinks (e.g. a symlinked dir with safe target permissions)?
   Answer: Use `os.stat` (follows symlinks) and check the target's mode and ownership-agnostic permission bits; the created files use `O_EXCL`, which refuses a pre-existing symlink at the file path.

4. For `sts_assume_role` / `sts_session_token`, is the temporary access key ID secret?
   Answer: No: it is an identifier, returned alongside the expiry; the secret key and session token go only to the file (the file also holds the key ID so it is self-contained).

5. Should `server_info` report whether secrets-dir mode is on?
   Answer: Yes if `server_info` already reports other settings (e.g. read-only); report the mode as a boolean, not the path. Check `tools/meta.py` during planning.

# WACM Connect: sub-accounts (`wacm_sub_accounts.py`, 6 tools)

All tools call WACM Connect with HTTP Basic auth (`WASABI_WACM_USERNAME`, `WASABI_WACM_API_KEY`). When either variable is missing, every tool refuses with a message naming both.

- **Lists** take `page` (numbered from 0) and `size` and return `{"items", "next_token", "truncated"}` plus WACM's own paging fields such as `total`. `next_token` is the next page number, to pass back as `page`.
- **Filters** are sent as WACM's camelCase query parameters; `from_date` and `to_date` are sent as `from` and `to`. Parameters left unset aren't sent.
- **Errors:** WACM's `{error, code}` comes back as `HTTP <status>: <error>`. HTTP 429 (rate limit) is retried once after 5 seconds, then reported.
- **Kinds:** R reads; W refuses while the server is read-only; D also needs `confirm=true`. Guards run before any request.

| Tool | Kind | Parameters | Description |
|---|---|---|---|
| `wacm_sub_account_list` | R | `page`?, `size`?, `id`?, `name`?, `control_account_id`?, `governance_account_id`?, `status`?, `include_deleted`? | List WACM Connect sub-accounts with their quota settings (purchasedStorageTB, storageQuotaType), optionally filtered. Returns one page (pages start at 0); keys are never requested or returned. |
| `wacm_sub_account_get` | R | `sub_account_id` | Get one WACM Connect sub-account by ID, including its quota settings. Its keys are never returned. |
| `wacm_sub_account_create` | D | `name`, `wasabi_account_email`, `password`?, `send_welcome_email`?, `control_account_id`?, `channel_account_id`?, `is_trial`?, `trial_quota_tb`?, `trial_days`?, `purchased_storage_tb`?, `storage_quota_type`?, `confirm`? | Create a WACM Connect sub-account (a new Wasabi account) with an optional Soft or Hard storage quota; Hard needs purchased_storage_tb. Give a password or send_welcome_email=true. Returns the account and its new access_key_id and secret_access_key, shown only this once; with WASABI_MCP_SECRETS_DIR set, the keys go to a new 0600 file instead and `secrets_file` gives its path. Needs confirm=true. |
| `wacm_sub_account_update` | D | `sub_account_id`, `active`?, `trial_to_paid`?, `purchased_storage_tb`?, `storage_quota_type`?, `reset_keys`?, `reset_mfa`?, `confirm`? | Partly update a WACM Connect sub-account: enable or disable it (active), convert a trial to paid, change its quota, reset its keys (new keys aren't returned) or reset its MFA. Only the fields given are sent. Needs confirm=true. |
| `wacm_sub_account_delete` | D | `sub_account_id`, `confirm`? | Delete a WACM Connect sub-account and the Wasabi account behind it. Needs confirm=true. |
| `wacm_sub_account_bucket_list` | R | `sub_account_id`, `page`?, `size`?, `from_date`?, `to_date`?, `latest`?, `name`?, `bucket_number`?, `region`? | List a sub-account's buckets with their usage, optionally filtered by from_date/to_date (YYYY-MM-DD), latest, name, bucket number or region. Returns one page (pages start at 0). |

Parameters marked `?` are optional.

## Safety notes

- `wacm_sub_account_create`, `_update` and `_delete` are D: they need writes enabled and `confirm=true`.
- `wacm_sub_account_create` checks its input before calling WACM: `name` is 3–100 characters, `storage_quota_type="Hard"` needs `purchased_storage_tb`, and it needs `password` or `send_welcome_email=true`.
- `wacm_sub_account_create` returns `{"sub_account", "access_key_id", "secret_access_key"}`. The new keys are returned only here and only once; the secret is removed from `sub_account`. Copy it straight away. With `WASABI_MCP_SECRETS_DIR` set, it returns `{"sub_account", "access_key_id", "secrets_file"}` instead: the keys go to a new 0600 file, and `secrets_file` is `null` when WACM returned no secret key.
- `includeKeys` is never sent, and every other tool drops key and secret fields from WACM's responses. `reset_keys=true` on `wacm_sub_account_update` rotates the keys without returning the new ones.
- Example (scenario S6, a tenant with a 10 TB hard quota): `wacm_sub_account_create(name="tenant-acme", wasabi_account_email="ops@acme.example", send_welcome_email=true, purchased_storage_tb=10, storage_quota_type="Hard", confirm=true)`.

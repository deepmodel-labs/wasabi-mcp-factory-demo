# WACM Connect: standalone accounts and control accounts (`wacm_accounts.py`, 9 tools)

All tools call WACM Connect with HTTP Basic auth (`WASABI_WACM_USERNAME`, `WASABI_WACM_API_KEY`). When either variable is missing, every tool refuses with a message naming both.

- **Lists** take `page` (numbered from 0) and `size` and return `{"items", "next_token", "truncated"}` plus WACM's own paging fields such as `total`. `next_token` is the next page number, to pass back as `page`.
- **Filters** are sent as WACM's camelCase query parameters; `from_date` and `to_date` are sent as `from` and `to`. Parameters left unset aren't sent.
- **Errors:** WACM's `{error, code}` comes back as `HTTP <status>: <error>`. HTTP 429 (rate limit) is retried once after 5 seconds, then reported.
- **Kinds:** R reads; W refuses while the server is read-only; D also needs `confirm=true`. Guards run before any request.

| Tool | Kind | Parameters | Description |
|---|---|---|---|
| `wacm_standalone_account_list` | R | `page`?, `size`?, `id`?, `name`?, `email`?, `status`?, `wasabi_account_number`?, `partner_name`?, `company_name`? | List WACM Connect standalone accounts, optionally filtered. Returns one page (pages start at 0); next_token is the next page number. |
| `wacm_standalone_account_create` | W | `first_name`, `last_name`, `email`, `country`, `storage_amount`, `company_name`?, `phone_number`? | Create a WACM Connect standalone account. country comes from wacm_country_list and storage_amount is a band from wacm_storage_amount_list. Returns the new account. |
| `wacm_storage_amount_list` | R | — | List the storage amount bands accepted by wacm_standalone_account_create. |
| `wacm_country_list` | R | — | List the countries accepted by wacm_standalone_account_create. |
| `wacm_control_account_list` | R | `page`?, `size`? | List WACM Connect control accounts. Returns one page (pages start at 0); API keys are never requested or returned. |
| `wacm_control_account_get` | R | `control_account_id` | Get one WACM Connect control account by ID. Its API key is never returned. |
| `wacm_control_account_usage_list` | R | `page`?, `size`?, `governance_account_id`?, `control_account_id`?, `sub_account_id`?, `wasabi_account_number`?, `latest`?, `from_date`?, `to_date`? | List control account usage records (storage and transfer per day), optionally filtered by account, latest only, or a from_date/to_date range (YYYY-MM-DD). Returns one page (pages start at 0). |
| `wacm_control_account_usage_get` | R | `utilization_id` | Get one control account usage record by its utilization ID. |
| `wacm_control_account_bucket_list` | R | `control_account_id`, `page`?, `size`?, `from_date`?, `to_date`?, `latest`?, `name`?, `bucket_number`?, `region`? | List a control account's buckets with their usage, optionally filtered by from_date/to_date (YYYY-MM-DD), latest, name, bucket number or region. Returns one page (pages start at 0). |

Parameters marked `?` are optional.

## Safety notes

- `wacm_standalone_account_create` is W: it refuses while the server is read-only.
- Control account API keys are never requested (`includeApiKey` is never sent) and any key field WACM returns is dropped.

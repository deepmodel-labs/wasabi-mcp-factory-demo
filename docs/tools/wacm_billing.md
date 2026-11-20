# WACM Connect: invoices and usages (`wacm_billing.py`, 4 tools)

All tools call WACM Connect with HTTP Basic auth (`WASABI_WACM_USERNAME`, `WASABI_WACM_API_KEY`). When either variable is missing, every tool refuses with a message naming both.

- **Lists** take `page` (numbered from 0) and `size` and return `{"items", "next_token", "truncated"}` plus WACM's own paging fields such as `total`. `next_token` is the next page number, to pass back as `page`.
- **Filters** are sent as WACM's camelCase query parameters; `from_date` and `to_date` are sent as `from` and `to`. Parameters left unset aren't sent.
- **Errors:** WACM's `{error, code}` comes back as `HTTP <status>: <error>`. HTTP 429 (rate limit) is retried once after 5 seconds, then reported.
- **Kinds:** R reads; W refuses while the server is read-only; D also needs `confirm=true`. Guards run before any request.

| Tool | Kind | Parameters | Description |
|---|---|---|---|
| `wacm_invoice_list` | R | `page`?, `size`?, `governance_account_id`?, `control_account_id`?, `sub_account_id`?, `invoice_id`?, `wasabi_account_number`?, `latest`?, `from_date`?, `to_date`? | List WACM Connect invoices, optionally filtered by governance, control or sub-account, invoice ID, Wasabi account number, latest, or a from_date/to_date range (YYYY-MM-DD). Returns one page (pages start at 0). |
| `wacm_invoice_get` | R | `invoice_id` | Get one WACM Connect invoice by ID. |
| `wacm_usage_list` | R | `page`?, `size`?, `governance_account_id`?, `control_account_id`?, `sub_account_id`?, `invoice_id`?, `wasabi_account_number`?, `latest`?, `from_date`?, `to_date`? | List WACM Connect usage records (storage and transfer per sub-account), with the same filters as wacm_invoice_list; latest=true gives each account's most recent record. Compare with wacm_sub_account_list quotas to find accounts near quota. Returns one page (pages start at 0). |
| `wacm_usage_get` | R | `utilization_id` | Get one WACM Connect usage record by its utilization ID. |

Parameters marked `?` are optional.

## Safety notes

- All tools are R.
- Scenario S7 (which sub-accounts are near quota): list sub-accounts with `wacm_sub_account_list` (quota in `purchasedStorageTB`, `storageQuotaType`), list this month's usage with `wacm_usage_list(latest=true, from_date=..., to_date=...)`, and compare each sub-account's storage with its quota.

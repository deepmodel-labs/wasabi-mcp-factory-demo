# Usage: Stats API tools

All four call `https://stats.wasabisys.com` with `Authorization: <access key>:<secret key>`. The keys must be root, or a sub-user with WasabiAccountStatsAccess or WasabiBucketStatsAccess; a 401/403 is explained in those terms. Stats are computed daily at 00:00 UTC. Storage fields are point-in-time, byte and call counters are per day, padded size applies the 4 KB minimum object size, and deleted storage reflects minimum-retention billing.

| Name | Kind | Description |
|---|---|---|
| `stats_account_utilization_list` | R | `GET /v1/standalone/utilizations`. Daily account records for `from_date`/`to_date` (YYYY-MM-DD; Wasabi defaults to the last month), `latest`, `include_regional`, `page_num`, `page_size` (1–100). Records as Wasabi returns them plus `*_human` sizes, with `page_num`, `page_size`, `next_page_num`, `page_count`, `record_count` and `truncated`. |
| `stats_bucket_utilization_list` | R | `GET /v1/standalone/utilizations/bucket`. Daily per-bucket records with the same parameters and page fields; `record_count`/`page_count` from page 0 are carried forward to later pages. |
| `stats_bucket_utilization_get` | R | `GET /v1/standalone/utilizations/bucket/{bucketName}` or `.../{bucketNumber}`. Exactly one of `bucket` or `bucket_number`, plus `latest`, the date range and page parameters. |
| `stats_usage_summary` | R | The latest day in one call: total active and deleted storage, objects, API calls, upload and download bytes (with `*_human`), and the top `top_n` (1–100, default 10) buckets by active storage. Reads up to 10 bucket pages of 100; `truncated` when there are more. |

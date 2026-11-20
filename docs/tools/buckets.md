# Bucket tools (`buckets.py`)

Kind: R read, W write (needs `WASABI_MCP_READ_ONLY=false`), D destructive (also needs `confirm=true`).
Tools that change a bucket send the request to the bucket's own region, looked up with
GetBucketLocation (and cached) unless `region` is given.

| Tool | Kind | Description |
|---|---|---|
| `bucket_list` | R | List buckets by name with their creation dates. With `include_region=true`, also each bucket's region. Paginated with `max_items` and `continuation_token`. |
| `bucket_get` | R | Summarise a bucket: its region, versioning status, whether Object Lock is enabled and its default retention rule if any. |
| `bucket_create` | W | Create a bucket in `region` (default: the server's region). `object_lock=true` enables Object Lock, which also turns on versioning and can never be turned off. |
| `bucket_delete` | D | Delete a bucket. `force=true` first deletes every object in it (Wasabi `force_delete`); Compliance and Object Lock retention still apply. |
| `bucket_rename` | D | Rename a bucket to `destination` with Wasabi's `MOVE` extension. Needs the `s3:CreateBucket` permission. |
| `bucket_versioning_get` | R | Show a bucket's versioning status (Enabled, Suspended or Unversioned) and whether MFA Delete is on. |
| `bucket_versioning_set` | W | Enable (`enabled=true`) or suspend (`enabled=false`) versioning. A versioned bucket can be suspended but never returns to unversioned. |
| `bucket_tagging_get` | R | Show a bucket's tags as a name-to-value map, or `{"configured": false}` when it has none. |
| `bucket_tagging_set` | W | Replace a bucket's whole tag set with `tags` (a name-to-value map). |
| `bucket_tagging_delete` | D | Remove every tag from a bucket. |
| `bucket_location_get` | R | Show the region a bucket lives in and the S3 service URL to use for it. |

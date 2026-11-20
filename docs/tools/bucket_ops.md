# Logging and inventory tools (`bucket_ops.py`)

Kind: R read, W write (needs `WASABI_MCP_READ_ONLY=false`), D destructive (also needs `confirm=true`).
Tools that change a bucket send the request to the bucket's own region, looked up with
GetBucketLocation (and cached) unless `region` is given. Inventory tools use Wasabi's
`?inventory` API.

| Tool | Kind | Description |
|---|---|---|
| `bucket_logging_get` | R | Show whether access logging is on for a bucket, and its target bucket and prefix. |
| `bucket_logging_set` | W | Turn on access logging into `target_bucket` (same account; surfaces LoggingBucketSameOwner) under `target_prefix`, or turn it off with `enabled=false`. Wasabi delivers logs best effort, with no SLA. |
| `bucket_inventory_list` | R | List a bucket's inventory configurations, paginated with `continuation_token` / `next_token`. |
| `bucket_inventory_get` | R | Show one inventory configuration by `inventory_id`, or `{"configured": false}` when none has that ID. |
| `bucket_inventory_set` | W | Create or replace an inventory configuration: a CSV report, Daily or Weekly, of Current or All versions under an optional `prefix`, written to `destination_bucket` / `destination_prefix`, with `optional_fields` from Wasabi's list (Size, LastModifiedDate, ETag, StorageClass, IsMultipartUploaded, ReplicationStatus, EncryptionStatus, ObjectLockRetainUntilDate, ObjectLockMode, ObjectLockLegalHoldStatus, ChecksumAlgorithm, ObjectAccessControlList, ObjectOwner). |
| `bucket_inventory_delete` | D | Delete one inventory configuration; reports already written stay. |

# Replication tools (`bucket_replication.py`)

Kind: R read, W write (needs `WASABI_MCP_READ_ONLY=false`), D destructive (also needs `confirm=true`).
Tools that change a bucket send the request to the bucket's own region, looked up with
GetBucketLocation (and cached) unless `region` is given.

| Tool | Kind | Description |
|---|---|---|
| `bucket_replication_get` | R | Show a bucket's replication role and rules, or `{"configured": false}` when it has no replication configuration. |
| `bucket_replication_set` | D | Replace a bucket's replication configuration, replicating new objects to `destination_bucket` (name or `arn:aws:s3:::name`). `rules` (`{id, prefix, tags, priority, enabled, delete_marker_replication}`) default to the whole bucket. Same account: leave `role` empty; cross-account: give `role`, `destination_account` and optionally `owner_translation`. Both buckets must have the same versioning state, which is checked first. |
| `bucket_replication_delete` | D | Delete a bucket's replication configuration; objects already replicated stay. |
| `bucket_replication_backfill` | W | Start a Wasabi job (`PUT ?manifestCreateAndProcess`) that replicates a bucket's existing objects, which replication alone doesn't copy. Returns the job ID. |

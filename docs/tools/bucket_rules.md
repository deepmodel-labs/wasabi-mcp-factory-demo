# CORS and lifecycle tools (`bucket_rules.py`)

Kind: R read, W write (needs `WASABI_MCP_READ_ONLY=false`), D destructive (also needs `confirm=true`).
Tools that change a bucket send the request to the bucket's own region, looked up with
GetBucketLocation (and cached) unless `region` is given.

| Tool | Kind | Description |
|---|---|---|
| `bucket_cors_get` | R | Show a bucket's CORS rules, or `{"configured": false}` when it has none; Wasabi then answers browsers with permissive default CORS headers. |
| `bucket_cors_set` | W | Replace a bucket's CORS rules. Each rule takes `allowed_origins` and `allowed_methods` (GET, PUT, POST, DELETE, HEAD), and optionally `allowed_headers`, `expose_headers`, `max_age_seconds` and `id`. |
| `bucket_cors_delete` | D | Delete a bucket's CORS rules, restoring Wasabi's default CORS behaviour. |
| `bucket_lifecycle_get` | R | Show a bucket's lifecycle rules, or `{"configured": false}` when it has none. |
| `bucket_lifecycle_set` | D | Replace all of a bucket's lifecycle rules with rules in S3 form: `ID`, `Status`, `Filter` (Prefix, Tag, And, ObjectSizeGreaterThan/LessThan) and `Expiration` (days, date or ExpiredObjectDeleteMarker), `NoncurrentVersionExpiration` or `AbortIncompleteMultipartUpload`. Transitions are rejected because Wasabi has one storage class. Rules run daily at 00:00 UTC and take 24–48 hours to apply, can't delete locked objects, and can't be set while MFA Delete is on. |
| `bucket_lifecycle_delete` | D | Delete all of a bucket's lifecycle rules. |

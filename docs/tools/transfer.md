# Presigned URLs (`transfer.py`)

PRD section 6.5, presign tools. Large or binary transfers go through these URLs so object data never passes through the model. URLs are signed by a client on `s3_endpoint_for(region)` for the bucket's region (from GetBucketLocation unless `region` is given), so their host is that region's endpoint. `expires_seconds` is 60 to 604,800 (7 days, Wasabi's maximum for IAM-user URLs), default 3,600, and is checked before any call. Multipart tools are added by another item.

| Tool | Kind | Description |
|---|---|---|
| `presign_get` | R | A presigned GET URL for an object, optionally a `version_id`. Returns `url`, `expires_seconds` and `expires_at`. |
| `presign_put` | W | A presigned PUT URL that lets its holder upload to the key. With `content_type`, the upload must send that header (returned in `headers`). |
| `presign_get_for_prefix` | R | Presigned GET URLs for every object under `prefix` whose key ends with `suffix` (optional), across as many listing pages as needed. At most `max_items` (default 100, at most 1,000) per call; each item has `key`, `size` and `url`. `next_token` resumes after the last key looked at. |

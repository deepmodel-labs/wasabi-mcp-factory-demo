# Presigned URLs and multipart (`transfer.py`)

PRD section 6.5. Large or binary transfers go through these URLs so object data never passes through the model. URLs are signed by a client on `s3_endpoint_for(region)` for the bucket's region (from GetBucketLocation unless `region` is given), so their host is that region's endpoint. `expires_seconds` is 60 to 604,800 (7 days, Wasabi's maximum for IAM-user URLs), default 3,600, and is checked before any call. Multipart tools let a client upload large objects in parts straight to Wasabi; guards and `part_count`/`expires_seconds` checks run before any call.

| Tool | Kind | Description |
|---|---|---|
| `presign_get` | R | A presigned GET URL for an object, optionally a `version_id`. Returns `url`, `expires_seconds` and `expires_at`. |
| `presign_put` | W | A presigned PUT URL that lets its holder upload to the key. With `content_type`, the upload must send that header (returned in `headers`). |
| `presign_get_for_prefix` | R | Presigned GET URLs for every object under `prefix` whose key ends with `suffix` (optional), across as many listing pages as needed. At most `max_items` (default 100, at most 1,000) per call; each item has `key`, `size` and `url`. `next_token` resumes after the last key looked at. |
| `multipart_upload_list` | R | Incomplete multipart uploads in a bucket, optionally under `prefix`; each item has `key`, `upload_id` and `initiated`. Wasabi deletes incomplete uploads after about 31 days. List envelope with `max_items`/`continuation_token`. |
| `multipart_part_list` | R | The parts uploaded so far for `upload_id`: `part_number`, `etag`, `size`, `last_modified`. List envelope. |
| `multipart_upload_abort` | D | Aborts `upload_id` and discards its parts. Needs `confirm=true`. |
| `multipart_upload_presign` | W | Starts a multipart upload and returns `upload_id` plus one presigned PUT URL per part (`part_count` 1 to 10,000, same `expires_seconds` limits). |
| `multipart_upload_complete` | W | Completes `upload_id` from `parts: [{part_number, etag}]` (any order; sorted before sending). Returns the object's `etag` and `version_id`. |

# Object reads (`objects_read.py`)

PRD section 6.2. Every tool takes an optional `region`; without it the bucket's region comes from GetBucketLocation, and the call goes to that region's endpoint.

| Tool | Kind | Description |
|---|---|---|
| `object_list` | R | List objects in a bucket, optionally under `prefix`. Each item has key, size, `size_human`, last modified and ETag; with `delimiter`, folder-like prefixes come back in `common_prefixes`. Takes `start_after`, `max_items` (default 100, at most 1,000) and `continuation_token`. |
| `object_version_list` | R | List object versions and delete markers, optionally under `prefix`. Each item has `version_id`, `is_latest` and `delete_marker`. `next_token` carries the key and version-id markers. |
| `object_get_info` | R | HeadObject: size, content type, ETag, last modified, user metadata, storage class, version, cache control and any Object Lock retention or legal hold. `version_id` optional. |
| `object_get_text` | R | Read a text object as UTF-8, up to `max_bytes` (default 256 KiB, at most 1 MiB). Larger objects are truncated with `truncated: true`, the full `size` and a note; binary content and `max_bytes` above 1 MiB are refused. Use `presign_get` for large or binary downloads. |
| `object_tagging_get` | R | An object's tags as a key-value map, or `{"configured": false}` when it has none. `version_id` optional. |
| `object_search` | R | Client-side search under `prefix` by `suffix`, `contains`, `min_size`, `max_size`, `modified_after` and `modified_before` (ISO 8601). Returns at most `max_items` matches, scans at most `max_scan` keys (default 10,000) and says when it stopped early; `next_token` resumes after the last key scanned. |

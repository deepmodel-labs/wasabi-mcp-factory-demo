# Object writes (`objects_write.py`)

PRD section 6.3. W tools refuse while the server is read-only; D tools also need `confirm=true`. Every tool takes an optional `region`; without it the bucket's region comes from GetBucketLocation.

| Tool | Kind | Description |
|---|---|---|
| `object_put_text` | W | Write a small UTF-8 text object (at most 1 MiB once encoded) with optional `content_type` and `metadata`. `overwrite: bool = False` refuses when the key exists (HeadObject first). Use `presign_put` for larger uploads. |
| `object_copy` | W | Server-side copy within a bucket or to another bucket in the same region, optionally from a source `version_id`. `overwrite` as above. Passing `metadata` or `content_type` replaces the copy's metadata. |
| `object_delete` | D | Delete an object, or one version with `version_id`. On a versioned bucket without `version_id` the result says a delete marker was created. |
| `object_delete_many` | D | Delete up to 1,000 listed `keys`, or up to `max_keys` (default 1,000, at most 10,000) keys under a non-empty `prefix`, in DeleteObjects batches of 1,000. Returns `deleted`, `errors` and `remaining` (more keys left under the prefix). |
| `object_tagging_set` | W | Replace an object's tag set with the given `tags` map. `version_id` optional. |
| `object_tagging_delete` | D | Remove every tag from an object. `version_id` optional. |
| `object_metadata_update` | W | Change `content_type`, `cache_control` or user `metadata` without re-uploading (CopyObject to itself with `MetadataDirective=REPLACE`). Fields not passed keep their current values. |

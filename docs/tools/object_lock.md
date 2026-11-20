# Object Lock, retention and legal hold tools (`object_lock.py`)

Kind: R read, W write (needs `WASABI_MCP_READ_ONLY=false`), D destructive (also needs `confirm=true`).
Tools that change a bucket or object send the request to the bucket's own region, looked up
with GetBucketLocation (and cached) unless `region` is given.

| Tool | Kind | Description |
|---|---|---|
| `bucket_object_lock_get` | R | Show whether Object Lock is enabled on a bucket and its default retention rule (mode, and days or years), if any. |
| `bucket_object_lock_set` | D | Set a bucket's default retention rule: `mode` COMPLIANCE or GOVERNANCE and either `days` or `years`; `clear_default=true` removes the rule instead. Object Lock itself can only be enabled when the bucket is created (`bucket_create` with `object_lock=true`). |
| `object_retention_get` | R | Show an object's (or one version's) retention mode and retain-until date, or `{"configured": false}` when it has none. |
| `object_retention_set` | D | Set an object's (or one version's) retention: `mode` until `retain_until` (ISO 8601, UTC if no offset). Compliance retention can't be shortened or removed by anyone; `bypass_governance=true` lets a permitted user shorten governance retention. |
| `object_legal_hold_get` | R | Show whether an object (or one version) is under legal hold. |
| `object_legal_hold_set` | D | Place (`on=true`) or release (`on=false`) a legal hold on an object or one version. A held object can't be deleted until the hold is released. |

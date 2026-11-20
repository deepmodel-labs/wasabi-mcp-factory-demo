# Object Lock and retention (`object_lock.py`)

PRD section 6.8, object-level tools. The bucket-level `bucket_object_lock_get` / `bucket_object_lock_set` belong to the same module and are added by another item. Object Lock only works on buckets created with it enabled. Every tool takes an optional `version_id` and `region`.

| Tool | Kind | Description |
|---|---|---|
| `object_retention_get` | R | An object's retention `mode` and `retain_until` time, or `{"configured": false}` when it has none. |
| `object_retention_set` | D | Set retention: `mode` GOVERNANCE or COMPLIANCE until `retain_until` (ISO 8601). `bypass_governance: bool = False` sends `x-amz-bypass-governance-retention` so GOVERNANCE retention can be shortened. COMPLIANCE retention can't be shortened by anyone; the result says so. |
| `object_legal_hold_get` | R | Whether an object has a legal hold (`status` ON or OFF), or `{"configured": false}` when none was ever set. |
| `object_legal_hold_set` | D | Turn a legal hold on or off with `on: bool`. While on, the version can't be deleted or overwritten. |

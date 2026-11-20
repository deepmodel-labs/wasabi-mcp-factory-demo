# Wasabi compliance (`compliance.py`)

Wasabi's compliance policy for buckets and per-object compliance settings (PRD 6.9). Every call
is a path-style, SigV4-signed `?compliance` request made with `rest.signed_s3`. Compliance
is separate from Object Lock: a bucket has one or the other. The writes look up the bucket's
region once with GetBucketLocation and cache it when `region` isn't given. The reads use
`region` or the settings region.

| Name | Kind | Description |
|---|---|---|
| `bucket_compliance_get` | R | Show a bucket's compliance policy: `GET /<bucket>?compliance`. Returns `status`, `lock_time`, `is_locked`, `retention_days`, `conditional_hold` and `delete_after_retention`. |
| `bucket_compliance_set` | D | Set a bucket's compliance policy, which applies at once to every object: `PUT /<bucket>?compliance` with a `<BucketComplianceConfiguration>` body. Takes `enabled`, `retention_days` (required, a non-negative integer), `conditional_hold`, `lock_time` (an ISO 8601 date or time, `now` or `off`) and `delete_after_retention`. Once locked, the settings can't be reduced without Wasabi Support, and Wasabi's `ComplianceSettingsLocked` error is returned with that advice. Needs `confirm=true`. |
| `object_compliance_get` | R | Show an object's compliance settings: `GET /<bucket>/<key>?compliance`. Returns `retention_time`, `conditional_hold`, `legal_hold` and `sha256`. Wasabi doesn't compute the SHA256 for multipart or composed objects, so `sha256` is null for them. |
| `object_compliance_set` | D | Change an object's compliance settings: `PUT /<bucket>/<key>?compliance` with an `<ObjectComplianceConfiguration>` body. `release_conditional_hold=true` releases the conditional hold, which can't be re-applied. `legal_hold` sets or clears a legal hold. `retention_time` (ISO 8601) can only extend retention: the current setting is read first, and an earlier time is refused before anything is changed. Needs `confirm=true`. |

## Safety

- Both set tools are destructive: they refuse in read-only mode and without `confirm=true`,
  before any request is sent.
- `retention_days`, `lock_time` and `retention_time` are validated before anything is sent.
  `object_compliance_set` refuses a call that changes nothing.
- A non-2xx response comes back as `ToolRefused("<Action> failed: <Code>: <Message>")` with
  Wasabi's own error code and message. The actions are `GetBucketCompliance`,
  `PutBucketCompliance`, `GetObjectCompliance` and `PutObjectCompliance`.

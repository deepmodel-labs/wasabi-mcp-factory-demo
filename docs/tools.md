# Tool catalogue

All 166 tools, grouped by area, in the order of PRD section 6. Generated from
`docs/tools/*.md` by `uv run python -m tests.docs_tools`; edit those files, not this one.
Each module's file also describes its shared behaviour (regions, paging, errors).

Kinds: R reads. W writes and refuses while `WASABI_MCP_READ_ONLY` is true (the default).
D is destructive and also needs `confirm=true` on each call.

## Storage

### Bucket tools (`buckets.py`)

Details: [`docs/tools/buckets.md`](tools/buckets.md).

| Tool | Kind | Description |
|---|---|---|
| `bucket_list` | R | List buckets by name with their creation dates. With `include_region=true`, also each bucket's region. Paginated with `max_items` and `continuation_token`. |
| `bucket_get` | R | Summarise a bucket: its region, versioning status, whether Object Lock is enabled and its default retention rule if any. |
| `bucket_create` | W | Create a bucket in `region` (default: the server's region). `object_lock=true` enables Object Lock, which also turns on versioning and can never be turned off. |
| `bucket_delete` | D | Delete a bucket. `force=true` first deletes every object in it (Wasabi `force_delete`); Compliance and Object Lock retention still apply. |
| `bucket_rename` | D | Rename a bucket to `destination` with Wasabi's `MOVE` extension. Needs the `s3:CreateBucket` permission. |
| `bucket_versioning_get` | R | Show a bucket's versioning status (Enabled, Suspended or Unversioned) and whether MFA Delete is on. |
| `bucket_versioning_set` | W | Enable (`enabled=true`) or suspend (`enabled=false`) versioning. A versioned bucket can be suspended but never returns to unversioned. |
| `bucket_tagging_get` | R | Show a bucket's tags as a name-to-value map, or `{"configured": false}` when it has none. |
| `bucket_tagging_set` | W | Replace a bucket's whole tag set with `tags` (a name-to-value map). |
| `bucket_tagging_delete` | D | Remove every tag from a bucket. |
| `bucket_location_get` | R | Show the region a bucket lives in and the S3 service URL to use for it. |

### Object reads (`objects_read.py`)

Details: [`docs/tools/objects_read.md`](tools/objects_read.md).

| Tool | Kind | Description |
|---|---|---|
| `object_list` | R | List objects in a bucket, optionally under `prefix`. Each item has key, size, `size_human`, last modified and ETag; with `delimiter`, folder-like prefixes come back in `common_prefixes`. Takes `start_after`, `max_items` (default 100, at most 1,000) and `continuation_token`. |
| `object_version_list` | R | List object versions and delete markers, optionally under `prefix`. Each item has `version_id`, `is_latest` and `delete_marker`. `next_token` carries the key and version-id markers. |
| `object_get_info` | R | HeadObject: size, content type, ETag, last modified, user metadata, storage class, version, cache control and any Object Lock retention or legal hold. `version_id` optional. |
| `object_get_text` | R | Read a text object as UTF-8, up to `max_bytes` (default 256 KiB, at most 1 MiB). Larger objects are truncated with `truncated: true`, the full `size` and a note; binary content and `max_bytes` above 1 MiB are refused. Use `presign_get` for large or binary downloads. |
| `object_tagging_get` | R | An object's tags as a key-value map, or `{"configured": false}` when it has none. `version_id` optional. |
| `object_search` | R | Client-side search under `prefix` by `suffix`, `contains`, `min_size`, `max_size`, `modified_after` and `modified_before` (ISO 8601). Returns at most `max_items` matches, scans at most `max_scan` keys (default 10,000) and says when it stopped early; `next_token` resumes after the last key scanned. |

### Object writes (`objects_write.py`)

Details: [`docs/tools/objects_write.md`](tools/objects_write.md).

| Tool | Kind | Description |
|---|---|---|
| `object_put_text` | W | Write a small UTF-8 text object (at most 1 MiB once encoded) with optional `content_type` and `metadata`. `overwrite: bool = False` refuses when the key exists (HeadObject first). Use `presign_put` for larger uploads. |
| `object_copy` | W | Server-side copy within a bucket or to another bucket in the same region, optionally from a source `version_id`. `overwrite` as above. Passing `metadata` or `content_type` replaces the copy's metadata. |
| `object_delete` | D | Delete an object, or one version with `version_id`. On a versioned bucket without `version_id` the result says a delete marker was created. |
| `object_delete_many` | D | Delete up to 1,000 listed `keys`, or up to `max_keys` (default 1,000, at most 10,000) keys under a non-empty `prefix`, in DeleteObjects batches of 1,000. Returns `deleted`, `errors` and `remaining` (more keys left under the prefix). |
| `object_tagging_set` | W | Replace an object's tag set with the given `tags` map. `version_id` optional. |
| `object_tagging_delete` | D | Remove every tag from an object. `version_id` optional. |
| `object_metadata_update` | W | Change `content_type`, `cache_control` or user `metadata` without re-uploading (CopyObject to itself with `MetadataDirective=REPLACE`). Fields not passed keep their current values. |

### Wasabi object extensions (`objects_ext.py`)

Details: [`docs/tools/objects_ext.md`](tools/objects_ext.md).

| Name | Kind | Description |
|---|---|---|
| `object_rename` | D | Rename an object (all its versions) to the `destination` key in the same bucket: `MOVE /<bucket>/<key>` with `Destination: <destination>` and `Overwrite: true\|false`. `overwrite` defaults to false, so an existing destination isn't replaced. Needs `s3:PutObject` and `confirm=true`. |
| `object_rename_prefix` | D | Rename every key (all versions) under `prefix` so it starts with `destination` instead: `MOVE /<bucket>/<prefix>` with `X-Wasabi-Prefix: true`, `Overwrite` and `X-Wasabi-Quiet`. Returns Wasabi's per-key results as `moved` and `errors`, plus `source_prefix`, `destination_prefix` and `move_count`. With `overwrite=false`, keys whose destination exists are reported under `errors`; nothing is refused locally. Needs `confirm=true`. |
| `object_compose` | W | Create `key` by linking 1 to 32 `sources` in the same bucket, in order: `PUT /<bucket>/<key>?compose` with a `<ComposeRequest>` body. No data is copied. Customer-provided (SSE-C) keys aren't supported, and the returned ETag is a hash of the part MD5s, not the MD5 of the whole object. |
| `object_append` | W | Append `text` (at most 1 MiB as UTF-8) to an object: `PUT /<bucket>/<key>?append` with `Content-Type` (default `text/plain`). If Wasabi answers `NoSuchKey`, the object is created with a plain `PUT` of the same text and `created` is `true` (otherwise `false`); Wasabi has no conditional create, so a concurrent writer could race with that create. Returns the ETag, version ID, `created` and any `X-Wasabi-*` headers. An object can be appended to at most 1,023 times, and each append is billed as a small object. |

### Presigned URLs and multipart (`transfer.py`)

Details: [`docs/tools/transfer.md`](tools/transfer.md).

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

### Access tools: bucket policy and ACLs (`access.py`)

Details: [`docs/tools/access.md`](tools/access.md).

| Tool | Kind | Description |
|---|---|---|
| `bucket_policy_get` | R | Show a bucket's policy document as JSON, or `{"configured": false}` when the bucket has no policy. |
| `bucket_policy_set` | D | Replace a bucket's policy with `policy` (a dict or JSON string, validated before calling Wasabi). Account and canonical IDs in principals must be Wasabi's, not AWS's. |
| `bucket_policy_delete` | D | Delete a bucket's policy, removing every access it granted. |
| `bucket_acl_get` | R | Show a bucket's ACL: its owner and the list of grants. |
| `bucket_acl_set` | D | Replace a bucket's ACL with a `canned_acl` (private, public-read, public-read-write, authenticated-read) or explicit `grants` (`{"permission", and one of "id", "uri", "email"}`), and return the new ACL. Trial accounts and accounts created after 2023-03-13 can't make buckets public. |
| `object_acl_get` | R | Show an object's ACL (optionally of one `version_id`): its owner and grants. |
| `object_acl_set` | D | Replace an object's (or one version's) ACL with a `canned_acl` or explicit `grants`, as for `bucket_acl_set`, and return the new ACL. |

### CORS and lifecycle tools (`bucket_rules.py`)

Details: [`docs/tools/bucket_rules.md`](tools/bucket_rules.md).

| Tool | Kind | Description |
|---|---|---|
| `bucket_cors_get` | R | Show a bucket's CORS rules, or `{"configured": false}` when it has none; Wasabi then answers browsers with permissive default CORS headers. |
| `bucket_cors_set` | W | Replace a bucket's CORS rules. Each rule takes `allowed_origins` and `allowed_methods` (GET, PUT, POST, DELETE, HEAD), and optionally `allowed_headers`, `expose_headers`, `max_age_seconds` and `id`. |
| `bucket_cors_delete` | D | Delete a bucket's CORS rules, restoring Wasabi's default CORS behaviour. |
| `bucket_lifecycle_get` | R | Show a bucket's lifecycle rules, or `{"configured": false}` when it has none. |
| `bucket_lifecycle_set` | D | Replace all of a bucket's lifecycle rules with rules in S3 form: `ID`, `Status`, `Filter` (Prefix, Tag, And, ObjectSizeGreaterThan/LessThan) and `Expiration` (days, date or ExpiredObjectDeleteMarker), `NoncurrentVersionExpiration` or `AbortIncompleteMultipartUpload`. Transitions are rejected because Wasabi has one storage class. Rules run daily at 00:00 UTC and take 24–48 hours to apply, can't delete locked objects, and can't be set while MFA Delete is on. |
| `bucket_lifecycle_delete` | D | Delete all of a bucket's lifecycle rules. |

### Object Lock, retention and legal hold tools (`object_lock.py`)

Details: [`docs/tools/object_lock.md`](tools/object_lock.md).

| Tool | Kind | Description |
|---|---|---|
| `bucket_object_lock_get` | R | Show whether Object Lock is enabled on a bucket and its default retention rule (mode, and days or years), if any. |
| `bucket_object_lock_set` | D | Set a bucket's default retention rule: `mode` COMPLIANCE or GOVERNANCE and either `days` or `years`; `clear_default=true` removes the rule instead. Object Lock itself can only be enabled when the bucket is created (`bucket_create` with `object_lock=true`). |
| `object_retention_get` | R | Show an object's (or one version's) retention mode and retain-until date, or `{"configured": false}` when it has none. |
| `object_retention_set` | D | Set an object's (or one version's) retention: `mode` until `retain_until` (ISO 8601, UTC if no offset). Compliance retention can't be shortened or removed by anyone; `bypass_governance=true` lets a permitted user shorten governance retention. |
| `object_legal_hold_get` | R | Show whether an object (or one version) is under legal hold. |
| `object_legal_hold_set` | D | Place (`on=true`) or release (`on=false`) a legal hold on an object or one version. A held object can't be deleted until the hold is released. |

### Wasabi compliance (`compliance.py`)

Details: [`docs/tools/compliance.md`](tools/compliance.md).

| Name | Kind | Description |
|---|---|---|
| `bucket_compliance_get` | R | Show a bucket's compliance policy: `GET /<bucket>?compliance`. Returns `status`, `lock_time`, `is_locked`, `retention_days`, `conditional_hold` and `delete_after_retention`. |
| `bucket_compliance_set` | D | Set a bucket's compliance policy, which applies at once to every object: `PUT /<bucket>?compliance` with a `<BucketComplianceConfiguration>` body. Takes `enabled`, `retention_days` (required, a non-negative integer), `conditional_hold`, `lock_time` (an ISO 8601 date or time, `now` or `off`) and `delete_after_retention`. Once locked, the settings can't be reduced without Wasabi Support, and Wasabi's `ComplianceSettingsLocked` error is returned with that advice. Needs `confirm=true`. |
| `object_compliance_get` | R | Show an object's compliance settings: `GET /<bucket>/<key>?compliance`. Returns `retention_time`, `conditional_hold`, `legal_hold` and `sha256`. Wasabi doesn't compute the SHA256 for multipart or composed objects, so `sha256` is null for them. |
| `object_compliance_set` | D | Change an object's compliance settings: `PUT /<bucket>/<key>?compliance` with an `<ObjectComplianceConfiguration>` body. `release_conditional_hold=true` releases the conditional hold, which can't be re-applied. `legal_hold` sets or clears a legal hold. `retention_time` (ISO 8601) can only extend retention: the current setting is read first, and an earlier time is refused before anything is changed. Needs `confirm=true`. |

### Logging and inventory tools (`bucket_ops.py`)

Details: [`docs/tools/bucket_ops.md`](tools/bucket_ops.md).

| Tool | Kind | Description |
|---|---|---|
| `bucket_logging_get` | R | Show whether access logging is on for a bucket, and its target bucket and prefix. |
| `bucket_logging_set` | W | Turn on access logging into `target_bucket` (same account; surfaces LoggingBucketSameOwner) under `target_prefix`, or turn it off with `enabled=false`. Wasabi delivers logs best effort, with no SLA. |
| `bucket_inventory_list` | R | List a bucket's inventory configurations, paginated with `continuation_token` / `next_token`. |
| `bucket_inventory_get` | R | Show one inventory configuration by `inventory_id`, or `{"configured": false}` when none has that ID. |
| `bucket_inventory_set` | W | Create or replace an inventory configuration: a CSV report, Daily or Weekly, of Current or All versions under an optional `prefix`, written to `destination_bucket` / `destination_prefix`, with `optional_fields` from Wasabi's list (Size, LastModifiedDate, ETag, StorageClass, IsMultipartUploaded, ReplicationStatus, EncryptionStatus, ObjectLockRetainUntilDate, ObjectLockMode, ObjectLockLegalHoldStatus, ChecksumAlgorithm, ObjectAccessControlList, ObjectOwner). |
| `bucket_inventory_delete` | D | Delete one inventory configuration; reports already written stay. |

### Replication tools (`bucket_replication.py`)

Details: [`docs/tools/bucket_replication.md`](tools/bucket_replication.md).

| Tool | Kind | Description |
|---|---|---|
| `bucket_replication_get` | R | Show a bucket's replication role and rules, or `{"configured": false}` when it has no replication configuration. |
| `bucket_replication_set` | D | Replace a bucket's replication configuration, replicating new objects to `destination_bucket` (name or `arn:aws:s3:::name`). `rules` (`{id, prefix, tags, priority, enabled, delete_marker_replication}`) default to the whole bucket. Same account: leave `role` empty; cross-account: give `role`, `destination_account` and optionally `owner_translation`. Both buckets must have the same versioning state, which is checked first. |
| `bucket_replication_delete` | D | Delete a bucket's replication configuration; objects already replicated stay. |
| `bucket_replication_backfill` | W | Start a Wasabi job (`PUT ?manifestCreateAndProcess`) that replicates a bucket's existing objects, which replication alone doesn't copy. Returns the job ID. |

## Identity

### IAM users and login profiles (`iam_users`)

Details: [`docs/tools/iam_users.md`](tools/iam_users.md).

| Name | Kind | Description |
|---|---|---|
| `iam_user_list` | R | List IAM users, optionally under a path prefix such as `/contractors/`. Returns users with name, ID, ARN, path and created date, plus pagination. |
| `iam_user_get` | R | Summarise one IAM user: the user, their groups, attached and inline policies, and access keys (IDs, status and created date only, never secrets). |
| `iam_user_create` | W | Create an IAM user (no keys or password yet). Returns the new user with its ARN. |
| `iam_user_update` | W | Rename an IAM user and/or change their path. Returns the user's new name. |
| `iam_user_delete` | D | Delete an IAM user; needs `confirm=true`. With `cascade=true` it first removes the user's keys, login profile, group memberships, policies and MFA devices, and lists each step. |
| `iam_login_profile_get` | R | Show whether an IAM user has a console password (login profile), with its created date and reset flag. Returns `{"configured": false}` when there is none. |
| `iam_login_profile_create` | D | Give an IAM user a console password; needs `confirm=true`. A strong password is generated when none is given, and returned once as `password`. With `WASABI_MCP_SECRETS_DIR` set, the password goes to a new 0600 file instead and `secrets_file` gives its path. |
| `iam_login_profile_update` | D | Change an IAM user's console password and/or whether they must reset it at next sign-in; needs `confirm=true`. Never returns the password. |
| `iam_login_profile_delete` | D | Remove an IAM user's console password so they can't sign in to the console; needs `confirm=true`. Access keys keep working. |

### IAM access keys (`iam_keys`)

Details: [`docs/tools/iam_keys.md`](tools/iam_keys.md).

| Name | Kind | Description |
|---|---|---|
| `iam_access_key_list` | R | List access keys for a user (default: the caller), or for every user with `all_users=true` (root's keys show a blank user name). Returns key IDs, status and created dates, never secrets. |
| `iam_access_key_create` | D | Create an access key for a user (default: the caller); needs `confirm=true`. Returns `access_key_id` and `secret_access_key` once: the secret can't be retrieved again. With `WASABI_MCP_SECRETS_DIR` set, the secret goes to a new 0600 file instead and `secrets_file` gives its path. Users can have at most two keys. |
| `iam_access_key_update` | D | Activate (`active=true`) or deactivate (`active=false`) an access key; needs `confirm=true`. A deactivated key stops working at once but can be reactivated. |
| `iam_access_key_delete` | D | Delete an access key permanently; needs `confirm=true`. Anything still using the key stops working. |
| `iam_access_key_last_used` | R | Show when an access key was last used, and for which service and region. Returns the key's user name and last-used details. |

### IAM groups (`iam_groups`)

Details: [`docs/tools/iam_groups.md`](tools/iam_groups.md).

| Name | Kind | Description |
|---|---|---|
| `iam_group_list` | R | List IAM groups, optionally under a path prefix. Returns groups with name, ID, ARN, path and created date, plus pagination. |
| `iam_group_get` | R | Summarise one IAM group: the group, its member user names, and its attached and inline policies. |
| `iam_group_create` | W | Create an IAM group. Returns the new group with its ARN. |
| `iam_group_update` | W | Rename an IAM group and/or change its path. Returns the group's new name. |
| `iam_group_delete` | D | Delete an IAM group; needs `confirm=true`. With `cascade=true` it first removes the members and detaches or deletes its policies, and lists each step. |
| `iam_group_add_user` | D | Add an IAM user to a group, granting them the group's policies; needs `confirm=true`. |
| `iam_group_remove_user` | D | Remove an IAM user from a group, taking away the group's policies; needs `confirm=true`. |
| `iam_user_group_list` | R | List the groups an IAM user belongs to, plus pagination. |

### IAM roles and STS (`iam_roles`)

Details: [`docs/tools/iam_roles.md`](tools/iam_roles.md).

| Name | Kind | Description |
|---|---|---|
| `iam_role_list` | R | List IAM roles, optionally under a path prefix. Returns roles with name, ARN, trust policy (as JSON) and created date, plus pagination. |
| `iam_role_get` | R | Summarise one IAM role: the role with its trust policy as JSON, and its attached and inline policies. |
| `iam_role_create` | D | Create an IAM role with a trust policy (dict or JSON) saying who may assume it; needs `confirm=true`. max_session_duration is 3,600 to 43,200 seconds. Returns the new role. |
| `iam_role_delete` | D | Delete an IAM role; needs `confirm=true`. With `cascade=true` it first detaches and deletes the role's policies, and lists each step. |
| `iam_role_trust_policy_update` | D | Replace an IAM role's trust policy (dict or JSON), changing who may assume it; needs `confirm=true`. |
| `sts_caller_identity` | R | Show who this server is signed in as: the account ID, user ID and ARN. Never returns keys. |
| `sts_assume_role` | D | Assume an IAM role and get temporary credentials; needs `confirm=true`. duration_seconds is 900 to 43,200, and an optional inline policy narrows access. The credentials are returned once; with `WASABI_MCP_SECRETS_DIR` set, they go to a new 0600 file instead and `secrets_file` gives its path. Wasabi doesn't let the root account assume roles. |
| `sts_session_token` | D | Get temporary session credentials for the caller, optionally MFA-authenticated with mfa_serial and mfa_code; needs `confirm=true`. duration_seconds is 900 to 43,200. The credentials are returned once; with `WASABI_MCP_SECRETS_DIR` set, they go to a new 0600 file instead and `secrets_file` gives its path. |

### IAM managed policies (`iam_policies`)

Details: [`docs/tools/iam_policies.md`](tools/iam_policies.md).

| Name | Kind | Description |
|---|---|---|
| `iam_policy_list` | R | List managed policies: scope All, AWS (Wasabi's system policies) or Local (customer-managed); only_attached=true limits to policies in use. Returns names, ARNs, default versions and attachment counts, plus pagination. |
| `iam_policy_get` | R | Show a managed policy and its default version's document as JSON. |
| `iam_policy_create` | D | Create a customer-managed policy from a document (dict or JSON); needs `confirm=true`. Returns the new policy with its ARN. |
| `iam_policy_delete` | D | Delete a customer-managed policy; needs `confirm=true`. With `cascade=true` it first detaches it from every user, group and role and deletes its non-default versions, listing each step. |
| `iam_policy_version_list` | R | List a managed policy's versions (at most five) with which one is the default, plus pagination. |
| `iam_policy_version_get` | R | Show one version of a managed policy, with its document as JSON. |
| `iam_policy_version_create` | D | Add a new version of a managed policy (dict or JSON), optionally making it the default; needs `confirm=true`. A policy holds at most five versions. |
| `iam_policy_version_delete` | D | Delete a non-default version of a managed policy; needs `confirm=true`. |
| `iam_policy_version_set_default` | D | Make a version the default (active) one for a managed policy, changing access for everyone it's attached to; needs `confirm=true`. |
| `iam_policy_entities_list` | R | List the users, groups and roles a managed policy is attached to, optionally only one kind. Each item has type, name and ID; plus pagination. |

### IAM policy attachment, inline policies and ValidatePolicy (`iam_policy_bindings`)

Details: [`docs/tools/iam_policy_bindings.md`](tools/iam_policy_bindings.md).

| Name | Kind | Description |
|---|---|---|
| `iam_policy_attach` | D | Attach a managed policy to a user, group or role (principal_type), granting its permissions; needs `confirm=true`. |
| `iam_policy_detach` | D | Detach a managed policy from a user, group or role (principal_type), removing its permissions; needs `confirm=true`. |
| `iam_attached_policy_list` | R | List the managed policies attached to a user, group or role, with names and ARNs, plus pagination. |
| `iam_inline_policy_list` | R | List the names of the inline policies embedded in a user, group or role, plus pagination. |
| `iam_inline_policy_get` | R | Show one inline policy of a user, group or role, with its document as JSON. |
| `iam_inline_policy_put` | D | Create or replace an inline policy (dict or JSON) on a user, group or role; needs `confirm=true`. |
| `iam_inline_policy_delete` | D | Delete an inline policy from a user, group or role; needs `confirm=true`. |
| `iam_policy_validate` | R | Lint a policy with Wasabi's ValidatePolicy and, given use_action, use_resource and use_service (iam, sts or s3), evaluate it for the signer. `is_principal=true` (default) for an identity policy, false for a resource policy. Returns Wasabi's lint and evaluation result as JSON in `validation` (ALLOW, DENY or none, and any lint messages). A Wasabi error is returned as an error with its code and message, never as a passing result. Wasabi's exact ValidatePolicy encoding is unconfirmed: the request follows its docs (signed form POST, `PolicyDocument` as JSON, `IsPrincipal` `true`/`false`) and Wasabi's error is passed back as is. |

### IAM account settings and MFA (`iam_account`)

Details: [`docs/tools/iam_account.md`](tools/iam_account.md).

| Name | Kind | Description |
|---|---|---|
| `iam_account_summary` | R | Summarise IAM usage and quotas for the account: counts of users, groups, roles, policies and MFA devices, and their limits. |
| `iam_account_authorization_details` | R | Snapshot every user, group, role and managed policy with their memberships and policy documents (as JSON). filter limits it to User, Role, Group, LocalManagedPolicy and/or AWSManagedPolicy. Each item has a type; plus pagination. |
| `iam_password_policy_get` | R | Show the account's password policy for console users. Returns {"configured": false} when none is set. |
| `iam_password_policy_set` | D | Replace the account's password policy; needs `confirm=true`. Settings left out return to their defaults. Returns the resulting policy. |
| `iam_password_policy_delete` | D | Remove the account's password policy, so console passwords only need the defaults; needs `confirm=true`. |
| `iam_account_alias_list` | R | List the account's alias (used in the console sign-in URL), plus pagination. |
| `iam_account_alias_create` | W | Set the account's alias for the console sign-in URL. An account has at most one alias. |
| `iam_account_alias_delete` | D | Remove the account's alias, changing the console sign-in URL; needs `confirm=true`. |
| `iam_mfa_device_list` | R | List a user's MFA devices when user_name is given; otherwise list the account's virtual MFA devices, filtered by assignment_status (Assigned, Unassigned or Any). Returns serial numbers, users and enable dates, never seeds or QR codes. |
| `iam_user_mfa_get` | R | Show a user's (default: the caller's) MFA status with Wasabi's GetUserMFA: device details, plus the authentication date and age of the current credentials. |
| `iam_mfa_virtual_device_create` | D | Create a virtual (TOTP) MFA device, the only kind Wasabi supports; needs `confirm=true`. Returns the Base32 seed and a QR code PNG (base64) once, for the user to enrol in an authenticator app. With `WASABI_MCP_SECRETS_DIR` set, both go to one new 0600 JSON file instead and only `serial_number` and `secrets_file` are returned. |
| `iam_mfa_device_deactivate` | D | Deactivate a user's MFA device, and with delete=true also delete the virtual device; needs `confirm=true`. The user can then sign in without MFA. |

## Usage

### Usage: Stats API tools

Details: [`docs/tools/stats.md`](tools/stats.md).

| Name | Kind | Description |
|---|---|---|
| `stats_account_utilization_list` | R | `GET /v1/standalone/utilizations`. Daily account records for `from_date`/`to_date` (YYYY-MM-DD; Wasabi defaults to the last month), `latest`, `include_regional`, `page_num`, `page_size` (1–100). Records as Wasabi returns them plus `*_human` sizes, with `page_num`, `page_size`, `next_page_num`, `page_count`, `record_count` and `truncated`. |
| `stats_bucket_utilization_list` | R | `GET /v1/standalone/utilizations/bucket`. Daily per-bucket records with the same parameters and page fields; `record_count`/`page_count` from page 0 are carried forward to later pages. |
| `stats_bucket_utilization_get` | R | `GET /v1/standalone/utilizations/bucket/{bucketName}` or `.../{bucketNumber}`. Exactly one of `bucket` or `bucket_number`, plus `latest`, the date range and page parameters. |
| `stats_usage_summary` | R | The latest day in one call: total active and deleted storage, objects, API calls, upload and download bytes (with `*_human`), and the top `top_n` (1–100, default 10) buckets by active storage. Reads up to 10 bucket pages of 100; `truncated` when there are more. |

## Account governance

### WACM Connect: standalone accounts and control accounts (`wacm_accounts.py`, 9 tools)

Details: [`docs/tools/wacm_accounts.md`](tools/wacm_accounts.md).

| Tool | Kind | Parameters | Description |
|---|---|---|---|
| `wacm_standalone_account_list` | R | `page`?, `size`?, `id`?, `name`?, `email`?, `status`?, `wasabi_account_number`?, `partner_name`?, `company_name`? | List WACM Connect standalone accounts, optionally filtered. Returns one page (pages start at 0); next_token is the next page number. |
| `wacm_standalone_account_create` | W | `first_name`, `last_name`, `email`, `country`, `storage_amount`, `company_name`?, `phone_number`? | Create a WACM Connect standalone account. country comes from wacm_country_list and storage_amount is a band from wacm_storage_amount_list. Returns the new account. |
| `wacm_storage_amount_list` | R | — | List the storage amount bands accepted by wacm_standalone_account_create. |
| `wacm_country_list` | R | — | List the countries accepted by wacm_standalone_account_create. |
| `wacm_control_account_list` | R | `page`?, `size`? | List WACM Connect control accounts. Returns one page (pages start at 0); API keys are never requested or returned. |
| `wacm_control_account_get` | R | `control_account_id` | Get one WACM Connect control account by ID. Its API key is never returned. |
| `wacm_control_account_usage_list` | R | `page`?, `size`?, `governance_account_id`?, `control_account_id`?, `sub_account_id`?, `wasabi_account_number`?, `latest`?, `from_date`?, `to_date`? | List control account usage records (storage and transfer per day), optionally filtered by account, latest only, or a from_date/to_date range (YYYY-MM-DD). Returns one page (pages start at 0). |
| `wacm_control_account_usage_get` | R | `utilization_id` | Get one control account usage record by its utilization ID. |
| `wacm_control_account_bucket_list` | R | `control_account_id`, `page`?, `size`?, `from_date`?, `to_date`?, `latest`?, `name`?, `bucket_number`?, `region`? | List a control account's buckets with their usage, optionally filtered by from_date/to_date (YYYY-MM-DD), latest, name, bucket number or region. Returns one page (pages start at 0). |

### WACM Connect: channel accounts and their users (`wacm_channels.py`, 8 tools)

Details: [`docs/tools/wacm_channels.md`](tools/wacm_channels.md).

| Tool | Kind | Parameters | Description |
|---|---|---|---|
| `wacm_channel_account_list` | R | `page`?, `size`? | List WACM Connect channel accounts. Returns one page (pages start at 0); next_token is the next page number. |
| `wacm_channel_account_get` | R | `channel_account_id` | Get one WACM Connect channel account by ID. |
| `wacm_channel_account_create` | W | `name`, `contact_email`, `purchased_storage`?, `storage_quota_type`? | Create a WACM Connect channel account with an optional purchased storage amount (TB) and Soft or Hard quota. Returns the new channel account. |
| `wacm_channel_account_update` | W | `channel_account_id`, `name`?, `contact_email`?, `purchased_storage`?, `storage_quota_type`? | Update a WACM Connect channel account's name, contact email, purchased storage (TB) or quota type. Only the fields given are sent. Returns the updated account. |
| `wacm_channel_account_delete` | D | `channel_account_id`, `confirm`? | Delete a WACM Connect channel account. This also unlinks all of its sub-accounts. Needs confirm=true. |
| `wacm_channel_user_create` | W | `channel_account_id`, `email`, `first_name`, `last_name`, `role`, `username`?, `password`? | Create a user on a WACM Connect channel account with the given role. Returns the new user (never its password). |
| `wacm_channel_user_update` | D | `user_id`, `role`?, `active`?, `reset_mfa`?, `password`?, `confirm`? | Change a channel account user's role, enable or disable them, reset their MFA or set a new password. Changes access, so it needs confirm=true. |
| `wacm_channel_user_delete` | D | `user_id`, `confirm`? | Delete a channel account user. Needs confirm=true. |

### WACM Connect: sub-accounts (`wacm_sub_accounts.py`, 6 tools)

Details: [`docs/tools/wacm_sub_accounts.md`](tools/wacm_sub_accounts.md).

| Tool | Kind | Parameters | Description |
|---|---|---|---|
| `wacm_sub_account_list` | R | `page`?, `size`?, `id`?, `name`?, `control_account_id`?, `governance_account_id`?, `status`?, `include_deleted`? | List WACM Connect sub-accounts with their quota settings (purchasedStorageTB, storageQuotaType), optionally filtered. Returns one page (pages start at 0); keys are never requested or returned. |
| `wacm_sub_account_get` | R | `sub_account_id` | Get one WACM Connect sub-account by ID, including its quota settings. Its keys are never returned. |
| `wacm_sub_account_create` | D | `name`, `wasabi_account_email`, `password`?, `send_welcome_email`?, `control_account_id`?, `channel_account_id`?, `is_trial`?, `trial_quota_tb`?, `trial_days`?, `purchased_storage_tb`?, `storage_quota_type`?, `confirm`? | Create a WACM Connect sub-account (a new Wasabi account) with an optional Soft or Hard storage quota; Hard needs purchased_storage_tb. Give a password or send_welcome_email=true. Returns the account and its new access_key_id and secret_access_key, shown only this once; with WASABI_MCP_SECRETS_DIR set, the keys go to a new 0600 file instead and `secrets_file` gives its path. Needs confirm=true. |
| `wacm_sub_account_update` | D | `sub_account_id`, `active`?, `trial_to_paid`?, `purchased_storage_tb`?, `storage_quota_type`?, `reset_keys`?, `reset_mfa`?, `confirm`? | Partly update a WACM Connect sub-account: enable or disable it (active), convert a trial to paid, change its quota, reset its keys (new keys aren't returned) or reset its MFA. Only the fields given are sent. Needs confirm=true. |
| `wacm_sub_account_delete` | D | `sub_account_id`, `confirm`? | Delete a WACM Connect sub-account and the Wasabi account behind it. Needs confirm=true. |
| `wacm_sub_account_bucket_list` | R | `sub_account_id`, `page`?, `size`?, `from_date`?, `to_date`?, `latest`?, `name`?, `bucket_number`?, `region`? | List a sub-account's buckets with their usage, optionally filtered by from_date/to_date (YYYY-MM-DD), latest, name, bucket number or region. Returns one page (pages start at 0). |

### WACM Connect: members (`wacm_members.py`, 5 tools)

Details: [`docs/tools/wacm_members.md`](tools/wacm_members.md).

| Tool | Kind | Parameters | Description |
|---|---|---|---|
| `wacm_member_list` | R | `page`?, `size`?, `id`?, `status`?, `username`?, `sub_account_id`? | List WACM Connect members, optionally filtered by ID, status, username or sub-account. Returns one page (pages start at 0). |
| `wacm_member_get` | R | `member_id` | Get one WACM Connect member by ID. |
| `wacm_member_create` | D | `first_name`, `last_name`, `email`, `password`, `member_role`, `username`?, `password_reset_required`?, `confirm`? | Create a WACM Connect member with the given role, granting them access. Returns the new member (never its password). Needs confirm=true. |
| `wacm_member_update` | D | `member_id`, `member_role`?, `reset_mfa`?, `password`?, `active`?, `reinstate`?, `confirm`? | Change a WACM Connect member's role, reset their MFA, set a password, enable or disable them, or reinstate them. Only the fields given are sent. Needs confirm=true. |
| `wacm_member_delete` | D | `member_id`, `confirm`? | Delete a WACM Connect member. Needs confirm=true. |

### WACM Connect: invoices and usages (`wacm_billing.py`, 4 tools)

Details: [`docs/tools/wacm_billing.md`](tools/wacm_billing.md).

| Tool | Kind | Parameters | Description |
|---|---|---|---|
| `wacm_invoice_list` | R | `page`?, `size`?, `governance_account_id`?, `control_account_id`?, `sub_account_id`?, `invoice_id`?, `wasabi_account_number`?, `latest`?, `from_date`?, `to_date`? | List WACM Connect invoices, optionally filtered by governance, control or sub-account, invoice ID, Wasabi account number, latest, or a from_date/to_date range (YYYY-MM-DD). Returns one page (pages start at 0). |
| `wacm_invoice_get` | R | `invoice_id` | Get one WACM Connect invoice by ID. |
| `wacm_usage_list` | R | `page`?, `size`?, `governance_account_id`?, `control_account_id`?, `sub_account_id`?, `invoice_id`?, `wasabi_account_number`?, `latest`?, `from_date`?, `to_date`? | List WACM Connect usage records (storage and transfer per sub-account), with the same filters as wacm_invoice_list; latest=true gives each account's most recent record. Compare with wacm_sub_account_list quotas to find accounts near quota. Returns one page (pages start at 0). |
| `wacm_usage_get` | R | `utilization_id` | Get one WACM Connect usage record by its utilization ID. |

## Server

### Server (`meta.py`)

Details: [`docs/tools/meta.md`](tools/meta.md).

| Tool | Kind | Description |
|---|---|---|
| `server_info` | R | The server's version, default region, read-only flag, endpoints, whether Wasabi keys and WACM Connect are configured, whether new secrets are written to `WASABI_MCP_SECRETS_DIR` (`secrets_dir_enabled`), and the loaded tool groups. Never returns credentials or the secrets directory path. |

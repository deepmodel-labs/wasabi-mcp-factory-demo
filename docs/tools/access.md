# Access tools: bucket policy and ACLs (`access.py`)

Kind: R read, W write (needs `WASABI_MCP_READ_ONLY=false`), D destructive (also needs `confirm=true`).
Tools that change a bucket send the request to the bucket's own region, looked up with
GetBucketLocation (and cached) unless `region` is given.

| Tool | Kind | Description |
|---|---|---|
| `bucket_policy_get` | R | Show a bucket's policy document as JSON, or `{"configured": false}` when the bucket has no policy. |
| `bucket_policy_set` | D | Replace a bucket's policy with `policy` (a dict or JSON string, validated before calling Wasabi). Account and canonical IDs in principals must be Wasabi's, not AWS's. |
| `bucket_policy_delete` | D | Delete a bucket's policy, removing every access it granted. |
| `bucket_acl_get` | R | Show a bucket's ACL: its owner and the list of grants. |
| `bucket_acl_set` | D | Replace a bucket's ACL with a `canned_acl` (private, public-read, public-read-write, authenticated-read) or explicit `grants` (`{"permission", and one of "id", "uri", "email"}`), and return the new ACL. Trial accounts and accounts created after 2023-03-13 can't make buckets public. |
| `object_acl_get` | R | Show an object's ACL (optionally of one `version_id`): its owner and grants. |
| `object_acl_set` | D | Replace an object's (or one version's) ACL with a `canned_acl` or explicit `grants`, as for `bucket_acl_set`, and return the new ACL. |

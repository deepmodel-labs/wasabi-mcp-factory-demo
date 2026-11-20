# IAM access keys (`iam_keys`)

| Name | Kind | Description |
|---|---|---|
| `iam_access_key_list` | R | List access keys for a user (default: the caller), or for every user with `all_users=true` (root's keys show a blank user name). Returns key IDs, status and created dates, never secrets. |
| `iam_access_key_create` | D | Create an access key for a user (default: the caller); needs `confirm=true`. Returns `access_key_id` and `secret_access_key` once: the secret can't be retrieved again. With `WASABI_MCP_SECRETS_DIR` set, the secret goes to a new 0600 file instead and `secrets_file` gives its path. Users can have at most two keys. |
| `iam_access_key_update` | D | Activate (`active=true`) or deactivate (`active=false`) an access key; needs `confirm=true`. A deactivated key stops working at once but can be reactivated. |
| `iam_access_key_delete` | D | Delete an access key permanently; needs `confirm=true`. Anything still using the key stops working. |
| `iam_access_key_last_used` | R | Show when an access key was last used, and for which service and region. Returns the key's user name and last-used details. |

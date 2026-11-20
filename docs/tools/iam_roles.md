# IAM roles and STS (`iam_roles`)

| Name | Kind | Description |
|---|---|---|
| `iam_role_list` | R | List IAM roles, optionally under a path prefix. Returns roles with name, ARN, trust policy (as JSON) and created date, plus pagination. |
| `iam_role_get` | R | Summarise one IAM role: the role with its trust policy as JSON, and its attached and inline policies. |
| `iam_role_create` | D | Create an IAM role with a trust policy (dict or JSON) saying who may assume it; needs `confirm=true`. max_session_duration is 3,600 to 43,200 seconds. Returns the new role. |
| `iam_role_delete` | D | Delete an IAM role; needs `confirm=true`. With `cascade=true` it first detaches and deletes the role's policies, and lists each step. |
| `iam_role_trust_policy_update` | D | Replace an IAM role's trust policy (dict or JSON), changing who may assume it; needs `confirm=true`. |
| `sts_caller_identity` | R | Show who this server is signed in as: the account ID, user ID and ARN. Never returns keys. |
| `sts_assume_role` | D | Assume an IAM role and get temporary credentials; needs `confirm=true`. duration_seconds is 900 to 43,200, and an optional inline policy narrows access. The credentials are returned once. Wasabi doesn't let the root account assume roles. |
| `sts_session_token` | D | Get temporary session credentials for the caller, optionally MFA-authenticated with mfa_serial and mfa_code; needs `confirm=true`. duration_seconds is 900 to 43,200. The credentials are returned once. |

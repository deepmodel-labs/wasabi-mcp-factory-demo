# IAM account settings and MFA (`iam_account`)

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

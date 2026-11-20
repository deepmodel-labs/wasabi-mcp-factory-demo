# IAM users and login profiles (`iam_users`)

| Name | Kind | Description |
|---|---|---|
| `iam_user_list` | R | List IAM users, optionally under a path prefix such as `/contractors/`. Returns users with name, ID, ARN, path and created date, plus pagination. |
| `iam_user_get` | R | Summarise one IAM user: the user, their groups, attached and inline policies, and access keys (IDs, status and created date only, never secrets). |
| `iam_user_create` | W | Create an IAM user (no keys or password yet). Returns the new user with its ARN. |
| `iam_user_update` | W | Rename an IAM user and/or change their path. Returns the user's new name. |
| `iam_user_delete` | D | Delete an IAM user; needs `confirm=true`. With `cascade=true` it first removes the user's keys, login profile, group memberships, policies and MFA devices, and lists each step. |
| `iam_login_profile_get` | R | Show whether an IAM user has a console password (login profile), with its created date and reset flag. Returns `{"configured": false}` when there is none. |
| `iam_login_profile_create` | D | Give an IAM user a console password; needs `confirm=true`. A strong password is generated when none is given, and returned once as `password`. |
| `iam_login_profile_update` | D | Change an IAM user's console password and/or whether they must reset it at next sign-in; needs `confirm=true`. Never returns the password. |
| `iam_login_profile_delete` | D | Remove an IAM user's console password so they can't sign in to the console; needs `confirm=true`. Access keys keep working. |

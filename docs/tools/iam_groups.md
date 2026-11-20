# IAM groups (`iam_groups`)

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

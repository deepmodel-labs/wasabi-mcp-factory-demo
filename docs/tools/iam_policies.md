# IAM managed policies (`iam_policies`)

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

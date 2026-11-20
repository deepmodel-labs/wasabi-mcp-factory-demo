# IAM policy attachment, inline policies and ValidatePolicy (`iam_policy_bindings`)

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

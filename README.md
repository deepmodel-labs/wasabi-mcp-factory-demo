# Wasabi MCP server (DeepModel factory demo)

> [!NOTE]
> This project is a demonstration of the **DeepModel software factory**. The factory built it from Wasabi's public documentation, starting from the product requirements in [docs/prd.md](docs/prd.md).
>
> It is **not Wasabi's official MCP server**, and Wasabi doesn't maintain or support it. For Wasabi's official MCP server, hosted or self-hosted, see [Wasabi MCP for AI](https://wasabi.com/innovation-labs/mcp-for-ai) and [Wasabi's MCP documentation](https://docs.wasabi.com/docs/hosted-wasabi-mcp-with-ai-systems).

An [MCP](https://modelcontextprotocol.io) server that gives AI agents tools for a Wasabi cloud storage account:

- **Storage:** buckets, objects, versioning, object lock, lifecycle, policies, CORS, tags and presigned URLs.
- **Usage:** storage and bandwidth utilisation, per account and per bucket.
- **Identity:** IAM users, groups, roles, policies and access keys.
- **Account governance:** control, channel and sub-accounts, members, invoices and usage, through WACM Connect.

It has 166 tools, listed by area under [Tools](#tools). The requirements they come from are in [docs/prd.md](docs/prd.md).

## Quick start

You'll need Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/deepmodel-labs/wasabi-mcp-factory-demo
cd wasabi-mcp-factory-demo
uv sync
WASABI_ACCESS_KEY_ID=… WASABI_SECRET_ACCESS_KEY=… uv run wasabi-mcp
```

To use it from an MCP client such as Claude Desktop or Claude Code, add it as a stdio server:

```json
{
  "mcpServers": {
    "wasabi": {
      "command": "uv",
      "args": ["--directory", "/path/to/wasabi-mcp-factory-demo", "run", "wasabi-mcp"],
      "env": {
        "WASABI_ACCESS_KEY_ID": "…",
        "WASABI_SECRET_ACCESS_KEY": "…",
        "WASABI_REGION": "us-east-1"
      }
    }
  }
}
```

`uv run wasabi-mcp --transport streamable-http` serves MCP over HTTP instead.

To check the setup, call the `server_info` tool. It shows the version, region, read-only mode, endpoints and whether keys and WACM Connect are configured, without returning any credentials.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `WASABI_ACCESS_KEY_ID`, `WASABI_SECRET_ACCESS_KEY` | (none) | Your Wasabi access key pair. |
| `WASABI_REGION` | `us-east-1` | The default region for storage calls. |
| `WASABI_MCP_READ_ONLY` | `true` | Write tools refuse to run until you set this to `false`. |
| `WASABI_WACM_USERNAME`, `WASABI_WACM_API_KEY` | (none) | WACM Connect credentials. Needed only for the account governance tools. |
| `WASABI_S3_ENDPOINT`, `WASABI_IAM_ENDPOINT`, `WASABI_STS_ENDPOINT`, `WASABI_STATS_ENDPOINT`, `WASABI_WACM_ENDPOINT` | Wasabi's public endpoints | Overrides, for private endpoints and tests. |

## Safety

- **The server starts read-only.** Every tool is marked read or write in its MCP annotations, and write tools refuse while `WASABI_MCP_READ_ONLY` is on.
- **Destructive tools also need `confirm=true` in the call.** These are deletes and changes to policies or access.
- **Credentials come only from the environment.** The server never logs or returns your configured credentials. Tools that create new keys (IAM access keys, sub-accounts) return the new secret once, and only when called with `confirm=true`.

## Tools

Kinds: R reads; W writes and refuses while `WASABI_MCP_READ_ONLY` is true; D is destructive and also needs `confirm=true`. [docs/tools.md](docs/tools.md) has every tool's kind and description; each link below goes to that group's page.

**Storage** (S3 and Wasabi's S3 extensions, 69 tools)

- [Buckets](docs/tools/buckets.md): `bucket_list`, `bucket_get`, `bucket_create`, `bucket_delete`, `bucket_rename`, `bucket_versioning_get`, `bucket_versioning_set`, `bucket_tagging_get`, `bucket_tagging_set`, `bucket_tagging_delete`, `bucket_location_get`
- [Object reads](docs/tools/objects_read.md): `object_list`, `object_version_list`, `object_get_info`, `object_get_text`, `object_tagging_get`, `object_search`
- [Object writes](docs/tools/objects_write.md): `object_put_text`, `object_copy`, `object_delete`, `object_delete_many`, `object_tagging_set`, `object_tagging_delete`, `object_metadata_update`
- [Wasabi object extensions](docs/tools/objects_ext.md): `object_rename`, `object_rename_prefix`, `object_compose`, `object_append`
- [Presigned URLs and multipart](docs/tools/transfer.md): `presign_get`, `presign_put`, `presign_get_for_prefix`, `multipart_upload_list`, `multipart_part_list`, `multipart_upload_abort`, `multipart_upload_presign`, `multipart_upload_complete`
- [Bucket policy and ACLs](docs/tools/access.md): `bucket_policy_get`, `bucket_policy_set`, `bucket_policy_delete`, `bucket_acl_get`, `bucket_acl_set`, `object_acl_get`, `object_acl_set`
- [CORS and lifecycle](docs/tools/bucket_rules.md): `bucket_cors_get`, `bucket_cors_set`, `bucket_cors_delete`, `bucket_lifecycle_get`, `bucket_lifecycle_set`, `bucket_lifecycle_delete`
- [Object Lock, retention and legal hold](docs/tools/object_lock.md): `bucket_object_lock_get`, `bucket_object_lock_set`, `object_retention_get`, `object_retention_set`, `object_legal_hold_get`, `object_legal_hold_set`
- [Wasabi compliance](docs/tools/compliance.md): `bucket_compliance_get`, `bucket_compliance_set`, `object_compliance_get`, `object_compliance_set`
- [Logging and inventory](docs/tools/bucket_ops.md): `bucket_logging_get`, `bucket_logging_set`, `bucket_inventory_list`, `bucket_inventory_get`, `bucket_inventory_set`, `bucket_inventory_delete`
- [Replication](docs/tools/bucket_replication.md): `bucket_replication_get`, `bucket_replication_set`, `bucket_replication_delete`, `bucket_replication_backfill`

**Identity** (IAM and STS, 60 tools)

- [Users and login profiles](docs/tools/iam_users.md): `iam_user_list`, `iam_user_get`, `iam_user_create`, `iam_user_update`, `iam_user_delete`, `iam_login_profile_get`, `iam_login_profile_create`, `iam_login_profile_update`, `iam_login_profile_delete`
- [Access keys](docs/tools/iam_keys.md): `iam_access_key_list`, `iam_access_key_create`, `iam_access_key_update`, `iam_access_key_delete`, `iam_access_key_last_used`
- [Groups](docs/tools/iam_groups.md): `iam_group_list`, `iam_group_get`, `iam_group_create`, `iam_group_update`, `iam_group_delete`, `iam_group_add_user`, `iam_group_remove_user`, `iam_user_group_list`
- [Roles and STS](docs/tools/iam_roles.md): `iam_role_list`, `iam_role_get`, `iam_role_create`, `iam_role_delete`, `iam_role_trust_policy_update`, `sts_caller_identity`, `sts_assume_role`, `sts_session_token`
- [Managed policies](docs/tools/iam_policies.md): `iam_policy_list`, `iam_policy_get`, `iam_policy_create`, `iam_policy_delete`, `iam_policy_version_list`, `iam_policy_version_get`, `iam_policy_version_create`, `iam_policy_version_delete`, `iam_policy_version_set_default`, `iam_policy_entities_list`
- [Policy attachment and inline policies](docs/tools/iam_policy_bindings.md): `iam_policy_attach`, `iam_policy_detach`, `iam_attached_policy_list`, `iam_inline_policy_list`, `iam_inline_policy_get`, `iam_inline_policy_put`, `iam_inline_policy_delete`, `iam_policy_validate`
- [Account settings and MFA](docs/tools/iam_account.md): `iam_account_summary`, `iam_account_authorization_details`, `iam_password_policy_get`, `iam_password_policy_set`, `iam_password_policy_delete`, `iam_account_alias_list`, `iam_account_alias_create`, `iam_account_alias_delete`, `iam_mfa_device_list`, `iam_user_mfa_get`, `iam_mfa_virtual_device_create`, `iam_mfa_device_deactivate`

**Usage** (the Stats API, 4 tools)

- [Stats API](docs/tools/stats.md): `stats_account_utilization_list`, `stats_bucket_utilization_list`, `stats_bucket_utilization_get`, `stats_usage_summary`

**Account governance** (WACM Connect, 32 tools)

- [Standalone and control accounts](docs/tools/wacm_accounts.md): `wacm_standalone_account_list`, `wacm_standalone_account_create`, `wacm_storage_amount_list`, `wacm_country_list`, `wacm_control_account_list`, `wacm_control_account_get`, `wacm_control_account_usage_list`, `wacm_control_account_usage_get`, `wacm_control_account_bucket_list`
- [Channel accounts and their users](docs/tools/wacm_channels.md): `wacm_channel_account_list`, `wacm_channel_account_get`, `wacm_channel_account_create`, `wacm_channel_account_update`, `wacm_channel_account_delete`, `wacm_channel_user_create`, `wacm_channel_user_update`, `wacm_channel_user_delete`
- [Sub-accounts](docs/tools/wacm_sub_accounts.md): `wacm_sub_account_list`, `wacm_sub_account_get`, `wacm_sub_account_create`, `wacm_sub_account_update`, `wacm_sub_account_delete`, `wacm_sub_account_bucket_list`
- [Members](docs/tools/wacm_members.md): `wacm_member_list`, `wacm_member_get`, `wacm_member_create`, `wacm_member_update`, `wacm_member_delete`
- [Invoices and usages](docs/tools/wacm_billing.md): `wacm_invoice_list`, `wacm_invoice_get`, `wacm_usage_list`, `wacm_usage_get`

**Server** (this server, 1 tool)

- [Server](docs/tools/meta.md): `server_info`


## Development

```bash
uv sync
```

**Lint and format check** (the same as CI):

```bash
uv run ruff check . && uv run ruff format --check .
```

**Unit tests.** These run against local stand-ins for Wasabi's APIs: moto for S3, IAM and STS, and respx for the Stats API, WACM Connect and Wasabi's S3 extensions. They need no credentials and make no calls to Wasabi. They include the catalogue contract tests (`tests/test_catalogue.py`), which check the server against `tests/catalogue.py`. Live tests are deselected by default.

```bash
uv run pytest -q
```

**Live tests** (`tests/live/`) run against a real Wasabi account. They cover buckets, objects, presigned URLs, lifecycle, IAM, the Stats API and scenarios S1, S2, S4 and S5. They create buckets, IAM users, groups and policies named `wasabi-mcp-test-<id>`, and delete them at the end of the run, even if a test fails. Use a throwaway account. The tests enable writes for their own server, and they skip unless `WASABI_LIVE=1` and both keys are set:

```bash
WASABI_LIVE=1 WASABI_ACCESS_KEY_ID=… WASABI_SECRET_ACCESS_KEY=… uv run pytest -q -m live
```

Stats records lag about a day, so the Stats tests check the response shape rather than the new buckets. WACM Connect has no live tests, because it needs a partner control account.

Each tool group is one module in `src/wasabi_mcp/tools/`, documented in `docs/tools/<module>.md`. The conventions every tool follows are at the top of `src/wasabi_mcp/tools/__init__.py`. After changing a module's doc, regenerate `docs/tools.md` with `uv run python -m tests.docs_tools`; a unit test checks that it is up to date. A new tool also needs a row in `tests/catalogue.py`.

## Licence

[Apache 2.0](LICENSE). Wasabi is a trademark of Wasabi Technologies, LLC. It's used here only to say which service this software works with.

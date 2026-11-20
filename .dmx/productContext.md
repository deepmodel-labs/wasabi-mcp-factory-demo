# Product context

## Why this exists
AI agents increasingly manage cloud storage: they find data, audit access, track usage and run lifecycle operations. Wasabi exposes S3-compatible storage with its own extensions, an AWS-compatible IAM API, a Stats API for usage, and WACM Connect for account hierarchies. This server puts them behind MCP tools with clear names, typed inputs, safe defaults and readable JSON results.

It's also a reference build for the DeepModel software factory. The repo, its issues and its pull requests show what the factory produced from a PRD.

## How it works (target, per docs/prd.md)
- An MCP client starts `wasabi-mcp` (stdio by default, or `--transport streamable-http`).
- Tools are grouped by area. Each group is one module that registers its tools on start-up and is found automatically.
- Tools call Wasabi through boto3 (S3, IAM, STS) or httpx helpers (Wasabi extensions, Stats, WACM Connect), and return plain JSON.
- Writes refuse while the server is read-only (`WASABI_MCP_READ_ONLY`, default `true`). Destructive tools also require `confirm=true`.
- New credentials (IAM access keys, console passwords, MFA seeds/QR codes, STS sessions, sub-account keys) are returned once by default. With `WASABI_MCP_SECRETS_DIR` set, they are written to private 0600 files instead, and only the file path and non-secret IDs are returned.
- `object_delete_many` refuses an empty prefix, so a batch delete can't empty a whole bucket by accident.
- Bucket and bucket-configuration tools are built (GH-3): buckets; policy and ACLs; CORS and lifecycle; Object Lock, retention and legal hold; logging and inventory; replication and backfill. That's 40 tools.
- Object and presigned-URL tools are built (GH-7): list, versions, head, search, text get (truncated at `max_bytes`, at most 1 MiB) and text put (at most 1 MiB), copy, delete, tags and metadata, plus `presign_get`, `presign_put` and `presign_get_for_prefix`. Presigned URLs use the endpoint for the bucket's region.
- IAM and STS tools are built (GH-4): users and login profiles; access keys; groups; roles and STS; managed policies and versions; policy attachments, inline policies and ValidatePolicy; account settings and MFA. That's 60 tools. New secrets (access keys, passwords, STS credentials, MFA seeds) come back only from the confirmed create call.
- Multipart tools are built (GH-15): list uploads and parts, abort, presign parts and complete. Large uploads go straight to Wasabi in parts. With them the full 166-tool catalogue is registered and checked by contract tests, and a live suite runs the core flows against a real account. The README and `docs/tools.md` list every tool by area.
- WACM Connect tools are built (GH-6): standalone and control accounts, channel accounts and users, sub-accounts with quotas, members, invoices and usages. That's 32 tools. Without `WASABI_WACM_USERNAME` and `WASABI_WACM_API_KEY` they refuse and name both variables.
- Wasabi doesn't support AWS flexible checksums, so every client only sends or validates them when an operation requires it (GH-18). `object_get_text` reads objects that were appended to, and a failure while reading the body comes back as a tool error.
- `object_append` to a key that doesn't exist creates the object with the text and returns `created: true` (`false` on a normal append) (GH-30). Only `NoSuchKey` triggers the create; Wasabi has no conditional create, so a concurrent writer can race with it (accepted).

## Who uses it
- Developers and operators who want an agent to work with their Wasabi account.
- Wasabi and other evaluators of the DeepModel software factory.

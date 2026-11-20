# Product context

## Why this exists
AI agents increasingly manage cloud storage: they find data, audit access, track usage and run lifecycle operations. Wasabi exposes S3-compatible storage with its own extensions, an AWS-compatible IAM API, a Stats API for usage, and WACM Connect for account hierarchies. This server puts them behind MCP tools with clear names, typed inputs, safe defaults and readable JSON results.

It's also a reference build for the DeepModel software factory. The repo, its issues and its pull requests show what the factory produced from a PRD.

## How it works (target, per docs/prd.md)
- An MCP client starts `wasabi-mcp` (stdio by default, or `--transport streamable-http`).
- Tools are grouped by area. Each group is one module that registers its tools on start-up and is found automatically.
- Tools call Wasabi through boto3 (S3, IAM, STS) or httpx helpers (Wasabi extensions, Stats, WACM Connect), and return plain JSON.
- Writes refuse while the server is read-only (`WASABI_MCP_READ_ONLY`, default `true`). Destructive tools also require `confirm=true`.
- Bucket and bucket-configuration tools are built (GH-3): buckets; policy and ACLs; CORS and lifecycle; Object Lock, retention and legal hold; logging and inventory; replication and backfill. That's 40 tools.
- IAM and STS tools are built (GH-4): users and login profiles; access keys; groups; roles and STS; managed policies and versions; policy attachments, inline policies and ValidatePolicy; account settings and MFA. That's 60 tools. New secrets (access keys, passwords, STS credentials, MFA seeds) come back only from the confirmed create call.

## Who uses it
- Developers and operators who want an agent to work with their Wasabi account.
- Wasabi and other evaluators of the DeepModel software factory.

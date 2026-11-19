# Project brief

**wasabi-mcp-factory-demo** is an independent, open-source MCP (Model Context Protocol) server for Wasabi cloud storage, written in Python from Wasabi's public documentation. It's also a demonstration of the DeepModel software factory: the factory builds all of the code from the requirements in `docs/prd.md`.

## Goals
- Give MCP clients (Claude Desktop, Claude Code and other agents) safe tools for a Wasabi account: storage (S3 and Wasabi's extensions), usage (Stats API), IAM and STS, and account governance (WACM Connect). That's 166 tools.
- Test every tool against local stand-ins (moto for S3/IAM/STS, respx for Wasabi's REST APIs), and the core flows against real Wasabi.
- Be safe by default: read-only until enabled, `confirm=true` for destructive tools, credentials only from the environment.

## Scope
- In scope: the self-hosted server (stdio and streamable HTTP), its tests, CI and docs.
- Out of scope: a hosted, multi-tenant or OAuth service; any code from Wasabi's official MCP server; a UI.
- `docs/prd.md` is the source of truth: the foundation (section 4), cross-cutting rules (section 5), the tool catalogue (section 6) and the ticket plan (section 9).

## Current state
The repository holds only the PRD, README, licence and this dmx setup. There's no code yet. Ticket 0 (PRD section 4) creates the foundation.

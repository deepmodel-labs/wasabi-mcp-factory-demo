## Open Learnings
- `uvicorn` is imported at module level in `server.py` so tests can monkeypatch `wasabi_mcp.server.uvicorn.run`; `parser.error` checks run before `build_server()`.
- Smoke-tested Phase 1 with Starlette `TestClient`: no token → 401 on `/mcp` and unknown paths, token → 200 on initialize, wrong Host → 421.

## Open Decisions

## Session Notes
- spec loop completed (outcome: success) — chained to plan (job `gh-19`).
- plan loop completed (outcome: success) — chained to dev (job `gh-19`).
- Phase 1 complete: loopback check, allowed hosts/origins, bearer-token middleware and remote opt-in in `server.py`.

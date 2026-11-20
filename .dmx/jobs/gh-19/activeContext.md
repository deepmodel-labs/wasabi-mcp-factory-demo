## Open Learnings
- Smoke-tested Phase 1 with Starlette `TestClient`: no token → 401 on `/mcp` and unknown paths, token → 200 on initialize, wrong Host → 421.

## Open Decisions

## Session Notes
- spec loop completed (outcome: success) — chained to plan (job `gh-19`).
- plan loop completed (outcome: success) — chained to dev (job `gh-19`).
- Phase 1 complete: loopback check, allowed hosts/origins, bearer-token middleware and remote opt-in in `server.py`.
- dev loop iterating (round 1) — repeat_until 'all_phases_complete' not yet met (job `gh-19`).
- Phase 2 complete: tests/test_http_security.py, README Remote HTTP section, PRD section 7 env vars, systemPatterns note.

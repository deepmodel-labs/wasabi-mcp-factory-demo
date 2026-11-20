## Open Learnings

## Open Decisions
- Blocked: `.github` is mounted as /dev/null (char device) in the runner container, so `.github/workflows/ci.yml` can't be created here. Developer decided: skip ci.yml in this PR; a maintainer adds it. The exact YAML is in `.dmx/jobs/gh-1/ci.yml` and goes in the PR description.

## Session Notes
- spec loop completed (outcome: success) — chained to plan (job `gh-1`).
- plan loop completed (outcome: success) — chained to dev (job `gh-1`).
- Project setup and core modules (config, safety, errors, clients) — Phase 1 complete.
- dev loop iterating (round 1) — repeat_until 'all_phases_complete' not yet met (job `gh-1`).
- REST helpers, tool framework, server_info and server entry point — Phase 2 complete.
- dev loop iterating (round 2) — repeat_until 'all_phases_complete' not yet met (job `gh-1`).
- Test harness, foundation tests, README note and CI YAML draft (ci.yml left to a maintainer) — Phase 3 complete.

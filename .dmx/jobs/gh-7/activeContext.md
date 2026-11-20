## Open Learnings
- `object_search` uses the last scanned key as `next_token` (ListObjectsV2 `StartAfter`), so it can stop mid-page and resume exactly; it also honours PRD `max_scan` (default 10,000).

## Open Decisions

## Session Notes
- spec loop completed (outcome: success) — chained to plan (job `gh-7`).
- plan loop completed (outcome: success) — chained to dev (job `gh-7`).
- Added object read tools (objects_read.py), moto tests and docs — Phase 1 complete.

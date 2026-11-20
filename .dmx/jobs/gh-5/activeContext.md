## Open Learnings
- stats.py: Wasabi bucket records name the bucket in `Bucket`; summary uses `PaddedStorageSizeBytes` as active storage.

## Open Decisions

## Session Notes
- spec loop completed (outcome: success) — chained to plan (job `gh-5`).
- plan: tasks.md written (2 phases, 16 tasks). No foundation human-size helper exists; stats.py gets a local `_human`. Query names assumed per Wasabi docs: from, to, latest, includeRegional, pageNum, pageSize.
- plan loop completed (outcome: success) — chained to dev (job `gh-5`).
- implement-next-phase: Phase 1 complete — stats.py with the four Stats tools, validators, pagination and summary.

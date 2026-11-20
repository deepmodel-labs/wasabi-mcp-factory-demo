## Open Learnings

## Open Decisions

## Session Notes
- spec loop completed (outcome: success) — chained to plan (job `gh-28`).
- plan loop completed (outcome: success) — chained to dev (job `gh-28`).
- Phase 1 complete: ValidatePolicy omits IsPrincipal for identity policies, parses Wasabi's malformed 200, returns `evaluation`, appends `<Detail>` to errors; tests and docs updated.
- dev loop completed (outcome: success) — chained to validate (job `gh-28`).
- validate loop completed (outcome: success) — chained to release (job `gh-28`).
- PR opened for: iam_policy_validate: omit IsPrincipal for identity policies; parse Wasabi's malformed response.
- release loop completed (outcome: success) (job `gh-28`).

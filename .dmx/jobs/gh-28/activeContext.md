## Open Learnings
- The live syntax-error case uses valid JSON with `Action: 42`, since `policy_json` rejects invalid JSON before any call; whether Wasabi calls that `MalformedPolicyDocument` is unmeasured.

## Open Decisions

## Session Notes
- spec loop completed (outcome: success) — chained to plan (job `gh-28`).
- plan loop completed (outcome: success) — chained to dev (job `gh-28`).
- Phase 1 complete: ValidatePolicy omits IsPrincipal for identity policies, parses Wasabi's malformed 200, returns `evaluation`, appends `<Detail>` to errors; tests and docs updated.

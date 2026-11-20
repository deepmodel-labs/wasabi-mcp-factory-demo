## Open Learnings

## Open Decisions

## Session Notes
- plan loop completed (outcome: success) — chained to dev (job `gh-18`).
- Disabled flexible checksums in the shared boto3 config, hardened object_get_text body reads, added regression, client and live tests (Phase 1 complete).
- dev loop completed (outcome: success) — chained to validate (job `gh-18`).
- validate loop failed required checks; dmx wrote Phase 2: Rework — validate, round 1 (job `gh-18`, task `d38f3df7`).
- validate loop started rework round 1 of 2 with dev (job `gh-18`).
- Kept GetBucketLocation errors in object_get_text by resolving the client before the GetObject try; added a missing-bucket test (Phase 2 rework complete).
- dev rework run completed (outcome: success) — back to validate (job `gh-18`).
- validate loop runs again after rework round 1 (job `gh-18`).
- validate loop passed after 1 rework round (job `gh-18`).
- validate loop completed (outcome: success) — chained to release (job `gh-18`).
- PR opened for: Fix object_get_text crash on appended objects by disabling flexible checksums.

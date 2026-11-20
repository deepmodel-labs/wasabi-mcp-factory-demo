## Open Learnings
- In this runner `.github/workflows/ci.yml` was readable (not a /dev/null mount); only writes are blocked.

## SHA Verification (2026-11-20, for PR description)
```
$ gh api repos/actions/checkout/git/ref/tags/v4.4.0 --jq '.object.type+" "+.object.sha'
commit 11d5960a326750d5838078e36cf38b85af677262
$ gh api repos/actions/checkout/commits/v4.4.0 --jq .sha
11d5960a326750d5838078e36cf38b85af677262
$ gh api repos/astral-sh/setup-uv/git/ref/tags/v6.8.0 --jq '.object.type+" "+.object.sha'
commit d0cc045d04ccac9d8b7881df0226f9e82c39688e
$ gh api repos/astral-sh/setup-uv/commits/v6.8.0 --jq .sha
d0cc045d04ccac9d8b7881df0226f9e82c39688e
```
- Both tags are lightweight (ref points straight at a commit); both methods agree.
- setup-uv action.yml at d0cc045 still declares the `python-version` input.
- v4.4.0 and v6.8.0 are the latest tags in their majors.
- `git apply --check` of ci.yml.patch on a copy of main's ci.yml passes; applied result matches `ci.yml` byte-for-byte (cmp).


## PR Draft Notes
- Acceptance criterion 3 (CI runs and passes on the pinned actions) can only be checked after a maintainer applies `.dmx/jobs/gh-20/ci.yml.patch` (`git apply .dmx/jobs/gh-20/ci.yml.patch`, or copy `.dmx/jobs/gh-20/ci.yml` over `.github/workflows/ci.yml`) and CI runs on it.
- `tests/test_ci_pinning.py` checks the handed-off file now; the check on the real ci.yml xfails at runtime until the patch is applied, then passes with no further edit.
- Follow-up: add Dependabot/Renovate config (in `.github/`) to keep the SHA pins current.
- Validation on branch: ruff check and ruff format --check pass; pytest -q: 1255 passed, 10 deselected, 1 xfailed.

## Open Decisions

## Session Notes
- spec loop completed (outcome: success) — chained to plan (job `gh-20`).
- plan loop completed (outcome: success) — chained to dev (job `gh-20`).
- Phase 1 complete: resolved and cross-checked SHAs (checkout v4.4.0, setup-uv v6.8.0), wrote hand-off ci.yml and ci.yml.patch, verified patch applies byte-identically.
- dev loop iterating (round 1) — repeat_until 'all_phases_complete' not yet met (job `gh-20`).
- Phase 2 complete: added tests/test_ci_pinning.py SHA-pinning guard; ruff and full pytest pass (1 expected xfail on the unpatched real ci.yml).

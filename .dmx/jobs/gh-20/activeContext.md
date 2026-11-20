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


## Open Decisions

## Session Notes
- spec loop completed (outcome: success) — chained to plan (job `gh-20`).
- plan loop completed (outcome: success) — chained to dev (job `gh-20`).
- Phase 1 complete: resolved and cross-checked SHAs (checkout v4.4.0, setup-uv v6.8.0), wrote hand-off ci.yml and ci.yml.patch, verified patch applies byte-identically.

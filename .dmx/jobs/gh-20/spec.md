---
ticket: gh-20
branch: chore-gh-20-pin-github-actions-in-ciyml-to-commit
summary: Pin GitHub Actions in ci.yml to commit SHAs and hand off the patch
ticketing: github-issues
---

# Pin GitHub Actions in ci.yml to commit SHAs and hand off the patch

**Ticket:** [#20](https://github.com/deepmodel-labs/wasabi-mcp-factory-demo/issues/20)
**Type:** chore
**Branch:** chore-gh-20-pin-github-actions-in-ciyml-to-commit

---

## Context
A security review of main (f78eca9) found that `.github/workflows/ci.yml` uses tags that can be moved: `actions/checkout@v4` and `astral-sh/setup-uv@v6`. If an upstream tag is moved, CI runs different code, and the `live` job has the Wasabi secrets. Pinning each action to a full commit SHA closes that gap. The factory runner can't write `.github/` (it's mounted as /dev/null, see techContext.md), so the change goes to a maintainer as exact YAML in the job folder and the PR description, the same way the original CI file was handed off. This is T5 of 5 in WMCP-B4 (WMCP-18).

## Scope
- Resolve the commit SHAs at hand-off time through the GitHub API (`gh api repos/{owner}/{repo}/git/ref/tags/{tag}`; for an annotated tag, also follow `git/tags/{sha}` to the commit):
  - `actions/checkout` **v4.4.0** (latest v4.x as of 2026-11-20)
  - `astral-sh/setup-uv` **v6.8.0** (latest v6.x as of 2026-11-20)
- Rewrite all four `uses:` entries (two in `test`, two in `live`) to `owner/repo@<40-char sha> # vX.Y.Z`.
- Hand-off files in this job folder: `ci.yml` (the full replacement file) and `ci.yml.patch` (a unified diff against main's `.github/workflows/ci.yml`, which `git apply` accepts).
- PR description: the diff, how to apply it (`git apply .dmx/jobs/<job>/ci.yml.patch`, or copy the file), and the commands that checked the SHAs and their output.
- A pytest guard (for example `tests/test_ci_pinning.py`) that reads `.github/workflows/ci.yml` and checks that every `uses:` is `@[0-9a-f]{40} # vX.Y.Z`. Mark it `xfail(strict=True)` until the maintainer applies the patch, so it doesn't break CI before then. Reading `.github/` may be impossible in the runner (/dev/null mount), so the test runs the same check on the handed-off `ci.yml` in the job folder or skips cleanly. See Q2.

## Out of Scope
- Moving to newer majors (`actions/checkout` v7, `astral-sh/setup-uv` v10): this needs its own review of any behaviour changes.
- Any other ci.yml edits (jobs, steps, permissions, triggers, the live-test exit-5 handling).
- Dependabot/Renovate config to keep the pins up to date (also lives in `.github/`). Note it as a follow-up.
- The other four WMCP-B4 items (HTTP auth, read-only presign, secret return, error detail).

## Technical Approach
- Only CI/infra is affected. No `wasabi_mcp` package code changes.
- Check the SHAs: compare `gh api repos/actions/checkout/commits/v4.4.0 --jq .sha` with the ref-based answer. Both must agree. Do the same for setup-uv. Record both in the PR.
- Make the patch with `diff -u` (or `git diff --no-index`) and the `a/.github/workflows/ci.yml` / `b/.github/workflows/ci.yml` paths, then check it applies cleanly: copy main's ci.yml into `$TMPDIR` and run `git apply --check` there.
- Keep `with: python-version: "3.12"` as it is. Check that setup-uv v6.8.0 still supports the `python-version` input (it has since v5/v6).
- Run `test_command` (ruff + pytest) on the branch. The third acceptance criterion (CI passes on the pinned actions) is checked after a maintainer applies the patch. The PR must say so.

---

## Questions
<!-- Answer each question before running /dmx/plan. -->

1. Pin to the newest patch in the current majors (v4.4.0, v6.8.0), or move to the newest majors (v7.0.1, v10.3.3) at the same time?
   Answer: Stay on the current majors. The item asks only for pinning, and upgrading majors could change CI behaviour. Do the upgrade separately.

2. The runner can't read or write `.github/` (/dev/null mount). Should the SHA-pinning guard test read the real `.github/workflows/ci.yml` (it then runs in real CI only after the patch lands) or the handed-off copy?
   Answer: Test the real file when it's readable and has content, else skip with a clear reason. Also check the handed-off `ci.yml` in the job folder so the factory can run the check now. Keep the test small. If it can't be made reliable, leave it out, since it's optional in the ticket.

3. Should the hand-off include the full file, the diff, or both?
   Answer: Both. The full file for copying, and the unified diff for `git apply`. This matches how the original CI file was handed off.

4. Is the `# vX.Y.Z` comment the exact patch tag (v4.4.0), not the major (v4)?
   Answer: Yes, the exact tag whose commit the SHA is, as the acceptance criteria require.

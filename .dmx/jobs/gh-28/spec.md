---
ticket: gh-28
branch: bug-gh-28-iampolicyvalidate-omit-isprincipal-for
summary: "iam_policy_validate: omit IsPrincipal for identity policies; parse Wasabi's malformed response"
ticketing: github-issues
---

# iam_policy_validate: omit IsPrincipal for identity policies; parse Wasabi's malformed response

**Ticket:** [#28](https://github.com/deepmodel-labs/wasabi-mcp-factory-demo/issues/28)
**Type:** bug
**Branch:** bug-gh-28-iampolicyvalidate-omit-isprincipal-for

---

## Context
WMCP-20. `iam_policy_validate` still fails on live Wasabi after #25 (gh-24). Live measurements of Wasabi's ValidatePolicy (2026-11-20) settle the questions gh-24 left open:

- Identity policy with `IsPrincipal` **omitted**: HTTP 200, valid. With `UseAction`/`UseResource`/`UseService` the response holds `<Evaluation>Allow</Evaluation>` or `<Evaluation>Deny</Evaluation>`.
- `IsPrincipal=true` **or** `false` on an identity policy: HTTP 400, `MalformedPolicy: Missing required field Principal`. So Wasabi treats any `IsPrincipal` field as "resource policy". Today the tool always sends it, which is the bug.
- Syntax error in the policy: HTTP 400, `MalformedPolicyDocument` with a `<Detail>` element alongside `Code`/`Message`.
- The 200 body is not well-formed XML: after `</ValidatePolicyResponse>` it carries `<ResponseMetadata>…</ResponseMetadata></CreatePolicyVersionResponse>`, so `rest.parse_xml` (`ElementTree.fromstring`) raises `ParseError` even on success.

## Scope
- `src/wasabi_mcp/tools/iam_policy_bindings.py` `iam_policy_validate`:
  - `is_principal=True` (default, identity policy): leave `IsPrincipal` out of the request entirely.
  - `is_principal=False` (resource policy): send `IsPrincipal=false`.
  - Return `{"validation": <ValidatePolicyResult as JSON, {} when absent>}` as today, plus `"evaluation": "Allow" | "Deny"` (Wasabi's text as is) when an `<Evaluation>` element is present anywhere in the `ValidatePolicyResponse` element.
  - Update the docstring: drop the "encoding is unconfirmed" sentence, describe the measured behaviour (IsPrincipal omitted for identity policies, `evaluation` field). Same text in `docs/tools/iam_policy_bindings.md`; regenerate `docs/tools.md` with `uv run python -m tests.docs_tools`.
- `src/wasabi_mcp/rest.py` (small shared change, allowed for a bug fix):
  - `iam_query`: when a 2xx body fails to parse, extract just the `<{action_name}Response …>…</{action_name}Response>` element from the text and parse that; if it isn't there, raise `ToolRefused` naming the action and saying the response wasn't valid XML (never an unhandled `ParseError`). Well-formed bodies (GetUserMFA, existing tests) take the unchanged path.
  - Errors (`_xml_error` for non-2xx and the 2xx `<Error>` check in `iam_query`): append Wasabi's `<Detail>` text when present, as `{action} failed: {Code}: {Message} ({Detail})`. Without `<Detail>` the message is unchanged.
- `tests/tools/test_iam_policy_bindings.py` (respx on `FAKE['iam']`), using bodies shaped like the live ones:
  - Default call: `IsPrincipal` absent from the form (`set(form)` has no `IsPrincipal`); `is_principal=False` sends `IsPrincipal=false`. Replace the existing true/false parametrised assertion and the "defaults to true" test.
  - 200 malformed body (ValidatePolicyResponse followed by stray `<ResponseMetadata>…</ResponseMetadata></CreatePolicyVersionResponse>`) without Use* fields → passes, `validation` returned, no `evaluation`.
  - Same malformed shape with `<Evaluation>Allow</Evaluation>` and with `Deny` → `evaluation` equals that value.
  - 400 `MalformedPolicyDocument` with `<Detail>` → `ToolError` whose message contains code, message and detail.
  - 400 `MalformedPolicy: Missing required field Principal` still reported.
  - Unparseable 200 with no `ValidatePolicyResponse` element → `ToolError`, not a crash.
- `tests/test_rest.py`: unit coverage for the `iam_query` fallback and `<Detail>` in error text if not already exercised through the tool tests.
- `tests/live/test_iam.py::test_iam_policy_validate_identity_policy`: lint-only call with the default succeeds; evaluated call with `use_action="s3:ListAllMyBuckets"`, `use_resource="*"`, `use_service="s3"` asserts `evaluation == "Allow"`; add a syntax-error policy expecting `ToolError` matching `MalformedPolicyDocument`.
- `.dmx/techContext.md`: replace the "still open" ValidatePolicy note with the confirmed behaviour.

## Out of Scope
- Changing the tool's argument names or the `is_principal` default.
- Normalising `Allow`/`Deny` case (returned as Wasabi sends it).
- Live verification of resource policies with `IsPrincipal=false` (not measured; behaviour follows Wasabi's docs).
- Other IAM tools and GetUserMFA behaviour (must keep passing).
- Retrying with different encodings.

## Technical Approach
- REST-path tool via `rest.iam_query` (httpx), so respx tests intercept it, following the existing `_validate_route` / `_form` helpers in the test file.
- In the tool, pass `"IsPrincipal": None if is_principal else "false"` — `iam_query` already drops `None` fields. `_flag` may become unused for this call; remove it if nothing else uses it.
- Malformed-body fallback lives in `iam_query` (it knows `action_name`), using a non-greedy regex / `str.find` over the decoded text for the opening `<{action}Response` and the closing `</{action}Response>`, then `parse_xml` on that slice. Namespaces are still stripped by `parse_xml`.
- `evaluation`: `next(el for el in root.iter() if _local(el.tag) == "Evaluation")` style lookup (reuse `_iam` helpers), added to the result only when present and non-empty.
- `<Detail>`: extend `_xml_error` and the 2xx-error branch in `iam_query` through one small helper so both format the same way. S3 errors use `_xml_error` too; appending Detail only when present keeps their messages unchanged.

---

## Questions
<!-- Answer each question before running /dmx/plan. -->

1. Where does `evaluation` go in the result: replace `validation`, or sit beside it?
   Answer: Beside it. Keep `{"validation": …}` for compatibility and add a top-level `"evaluation"` only when Wasabi returns `<Evaluation>`, so callers don't need to know Wasabi's nesting.

2. Should the malformed-response fallback be specific to ValidatePolicy or live in `rest.iam_query`?
   Answer: In `iam_query`, keyed on `{action_name}Response`, because that's where parsing happens and the fallback only triggers on a `ParseError`, so well-formed responses (GetUserMFA) are untouched.

3. Should `is_principal=True` still be sent if a user explicitly passes it?
   Answer: No. True and the default are the same thing (identity policy), and live Wasabi rejects any `IsPrincipal` on an identity policy, so it's always omitted. Only `false` is sent.

4. Should `<Detail>` be added for every REST-path XML error (S3 too) or only IAM?
   Answer: Everywhere `_xml_error` is used, but only when a `<Detail>` element is present, so existing messages and tests don't change.

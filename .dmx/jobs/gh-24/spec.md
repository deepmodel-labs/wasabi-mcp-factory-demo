---
ticket: gh-24
branch: bug-gh-24-fix-iampolicyvalidate-validatepolicy
summary: Fix iam_policy_validate ValidatePolicy request encoding for Wasabi
ticketing: github-issues
---

# Fix iam_policy_validate ValidatePolicy request encoding for Wasabi

**Ticket:** [#24](https://github.com/deepmodel-labs/wasabi-mcp-factory-demo/issues/24)
**Type:** bug
**Branch:** bug-gh-24-fix-iampolicyvalidate-validatepolicy

---

## Context
WMCP-12, T4 of 5 in WMCP-B3 (bugs found by live testing on Wasabi). On real Wasabi, every `iam_policy_validate` call fails with `MalformedPolicy: Missing required field Principal`, whatever `is_principal` and `use_action`/`use_resource`/`use_service` are set to, so the tool can't be used. A missing Principal is what Wasabi reports when it lints a resource (bucket) policy. So Wasabi probably isn't reading the `IsPrincipal=true` the tool sends, or is reading the policy differently. The respx tests pass because they only check the request against our own assumptions.

What Wasabi documents (https://docs.wasabi.com/apidocs/iam-and-sts-support, "Validating Policies"):
- Parameters: `PolicyDocument` ("the text of the policy document … formatted as JSON"), `IsPrincipal` ("the Boolean value 'true' if this policy is applied to a principal element (a user, role, or group). Otherwise, the policy is assumed to apply to a specific resource"), `UseAction`, `UseResource` (an ARN), `UseService` (iam, sts or s3; default iam).
- Wasabi evaluates the policy for the principal that signed the request and returns ALLOW, DENY or no status, plus error information about problems in the policy.
- The page doesn't show a request or response example for ValidatePolicy. https://docs.wasabi.com/apidocs/enhanced-account-security-management shows IAM API calls as `POST https://iam.wasabisys.com/` with the fields in the request body (`Action: CreateVirtualMFADevice`, `VirtualMFADeviceName: …`), signed with AWS Signature (SigV4).

What we send today (`tools/iam_policy_bindings.py` → `rest.iam_query`): a SigV4 (`iam`, us-east-1) form POST to `{iam_url}/`, `Content-Type: application/x-www-form-urlencoded; charset=utf-8`, with body fields `Action=ValidatePolicy`, `Version=2010-05-08`, `PolicyDocument=<compact JSON, URL-encoded once by urlencode>`, `IsPrincipal=true|false`, and `UseAction`/`UseResource`/`UseService` only when set. On paper this already matches the docs. So the real cause can't be confirmed from the docs alone and needs the live test.

## Scope
- `src/wasabi_mcp/tools/iam_policy_bindings.py` `iam_policy_validate`:
  - Check every part of the request against the docs: the parameter names (`PolicyDocument`, `IsPrincipal`, `UseAction`, `UseResource`, `UseService`), JSON encoded exactly once and URL-encoded exactly once, `IsPrincipal` as the lowercase string `true`/`false`, `is_principal` defaulting to true, and the optional fields left out when `None`.
  - Change the encoding where the evidence points to a difference (see Technical Approach), keeping it to the documented form.
  - Success: return Wasabi's lint and evaluation result as PRD 6.12 describes (ALLOW, DENY or none, plus lint messages), keeping the `{"validation": …}` shape.
  - Error: any non-2xx goes back as `ToolRefused` with Wasabi's error code and message (already `ValidatePolicy failed: <Code>: <Message>` via `rest.iam_query`). A 200 response whose body is an `<ErrorResponse>`/`<Error>` must also become an error, never a passing result.
  - If the encoding stays unconfirmed, say so in the tool docstring (and `docs/tools/iam_policy_bindings.md`; regenerate `docs/tools.md` with `uv run python -m tests.docs_tools`).
- `src/wasabi_mcp/rest.py` `iam_query`: only if needed (for example to send some fields in the query string, or to detect an error body on a 200). `GetUserMFA` must keep working.
- `tests/tools/test_iam_policy_bindings.py` (respx on `FAKE['iam']`):
  - The outgoing request for `is_principal` true and for false: method, path, content type, SigV4 scope, `Action`/`Version`, `PolicyDocument` decodes (one `parse_qs` plus one `json.loads`) to the original dict and isn't double-encoded, `IsPrincipal` exactly `true`/`false`.
  - `is_principal` left out → `IsPrincipal=true`.
  - `use_action`/`use_resource`/`use_service` present with the documented names when set, absent when not.
  - A mocked success response (ALLOW with lint messages, and a lint-only response) → the tool returns that result.
  - A mocked `MalformedPolicy` error response → `ToolError` whose message contains `MalformedPolicy` and Wasabi's message text.
- `tests/live/test_iam.py`: a `live`-marked test that validates `{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"s3:ListAllMyBuckets","Resource":"*"}]}` with the default `is_principal`, and with `use_action="s3:ListAllMyBuckets"`, `use_resource="*"` (or the ARN form) and `use_service="s3"`, expecting no error and an ALLOW result. Skipped unless `WASABI_LIVE=1` (the existing live conftest).
- techContext.md note on what was confirmed (or still open) about ValidatePolicy's encoding and response shape.

## Out of Scope
- The other WMCP-B3 bugs: `bucket_tagging_get` after delete, `object_get_text` after append, `object_append` to a new key, and network errors without a reason in REST-path tools (except that ValidatePolicy errors must name Wasabi's code and message).
- Other IAM tools, `GetUserMFA`'s behaviour, and switching ValidatePolicy to boto3 (botocore doesn't model it).
- Retrying with a different encoding when Wasabi rejects the first one (it would hide real policy errors).

## Technical Approach
- This is a REST-path tool (`rest.iam_query`, httpx), so respx can intercept it, as the ticket assumed. Bug-fix ticket: a small change to the shared `rest.py` is allowed if needed.
- Candidate causes, best documented first:
  1. `IsPrincipal` isn't being read. Check that the value is exactly `true`, that the field reaches Wasabi in the signed body, and whether Wasabi reads Query API parameters from the URL query string rather than the POST body. If it does, ValidatePolicy's parameters (or all of them) move to the query string of a signed POST, which `iam_query` could support with an optional `query` argument.
  2. `PolicyDocument` is encoded twice: a JSON string passed through `json.dumps` again, or URL-encoded twice. `policy_json` should parse a string and re-dump it, so make sure only a single encoding reaches the wire, and test it.
  3. Wasabi's own (non-AWS) parameter spelling. The docs use `IsPrincipal` and `true`, so keep that unless live evidence says otherwise.
- Ship the encoding that best follows the docs (form POST to `/`, `IsPrincipal=true`, policy JSON encoded once). If live access doesn't confirm it, add a sentence to the tool description like "Wasabi's exact encoding for ValidatePolicy is unconfirmed; Wasabi's error is returned as is."
- Parse the result with the existing `_iam.query_result(root, "ValidatePolicy")`. Live tests should confirm the real response shape (techContext already flags it).
- Tests follow the existing respx pattern in the file (`parse_qs(request.content.decode())`). Error tests use `pytest.raises(ToolError, match=...)` without `^`.

---

## Questions
<!-- Answer each question before running /dmx/plan. -->

1. The documented encoding is what we already send. Without live access, should the fix change the wire format at all (for example moving parameters to the query string), or only tighten tests, error handling and the description?
   Answer: Only change what the docs or code inspection show is wrong (for example a double encoding). Otherwise keep the documented form-body encoding, make the error clear, note the open question in the description, and leave the decision to the live test (as the ticket says: don't block on it).

2. Should a 200 response with an `<Error>` body (or a `ValidatePolicyResult` holding an error) count as a failure?
   Answer: Yes, if it holds an error code. It becomes `ToolRefused` with the code and message, never a passing `validation`.

3. In the live test, should `use_resource` be `*` or an ARN, and what result should it assert?
   Answer: Use `use_action="s3:ListAllMyBuckets"`, `use_service="s3"`, `use_resource="*"`. Assert that no error is raised and that the result contains ALLOW. A lint-only call with the default `is_principal` must also succeed.

4. Does `rest.iam_query` need to change, and how do we keep `GetUserMFA` safe?
   Answer: Only if the fix needs it. Any new parameter (such as `query=`) defaults to today's behaviour, and the existing `GetUserMFA` and `test_rest.py` tests must still pass.

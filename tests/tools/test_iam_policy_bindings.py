import json
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from tests.conftest import FAKE
from tests.helpers import ToolError, call
from tests.tools.iam_support import READ_ONLY_POLICY, assert_no_secrets, client_error, stubbed

PRINCIPALS = [("user", "alice"), ("group", "readers"), ("role", "reader")]
TRUST = {
    "Version": "2012-10-17",
    "Statement": [
        {"Effect": "Allow", "Principal": {"AWS": "arn:aws:iam::1:root"}, "Action": "sts:AssumeRole"}
    ],
}

VALIDATE_RESPONSE = (
    b'<ValidatePolicyResponse xmlns="https://iam.amazonaws.com/doc/2010-05-08/">'
    b"<ValidatePolicyResult><Result>ALLOW</Result>"
    b"<Messages><member><Severity>WARNING</Severity><Text>Resource is broad</Text></member>"
    b"</Messages></ValidatePolicyResult>"
    b"<ResponseMetadata><RequestId>r1</RequestId></ResponseMetadata></ValidatePolicyResponse>"
)
IAM_ERROR = (
    b"<ErrorResponse><Error><Code>MalformedPolicyDocument</Code>"
    b"<Message>Policy has no statements</Message></Error></ErrorResponse>"
)


@pytest.fixture
def stub(rest_settings, monkeypatch):
    yield from stubbed(rest_settings, monkeypatch)


@pytest.fixture
async def principals(server):
    await call(server, "iam_user_create", user_name="alice")
    await call(server, "iam_group_create", group_name="readers")
    await call(
        server, "iam_role_create", role_name="reader", assume_role_policy=TRUST, confirm=True
    )
    policy = await call(
        server,
        "iam_policy_create",
        policy_name="ro",
        policy_document=READ_ONLY_POLICY,
        confirm=True,
    )
    return policy["policy"]["Arn"]


def _binding(kind, name, **extra):
    return {"principal_type": kind, "principal_name": name, **extra}


# iam_policy_attach / iam_attached_policy_list


@pytest.mark.parametrize(("kind", "name"), PRINCIPALS)
async def test_policy_attach_and_list(server, principals, kind, name):
    arn = principals
    result = await call(
        server, "iam_policy_attach", **_binding(kind, name, policy_arn=arn), confirm=True
    )
    assert result == {"ok": True, **_binding(kind, name, policy_arn=arn)}
    attached = await call(server, "iam_attached_policy_list", **_binding(kind, name))
    assert attached == {
        "items": [{"PolicyName": "ro", "PolicyArn": arn}],
        "next_token": None,
        "truncated": False,
    }


async def test_policy_attach_missing_principal(server, principals):
    with pytest.raises(ToolError, match="AttachUserPolicy failed: NoSuchEntity"):
        await call(
            server,
            "iam_policy_attach",
            **_binding("user", "ghost", policy_arn=principals),
            confirm=True,
        )


async def test_policy_attach_rejects_unknown_principal_type(server):
    with pytest.raises(ToolError):
        await call(
            server,
            "iam_policy_attach",
            **_binding("bucket", "x", policy_arn="arn:aws:iam::1:policy/p"),
            confirm=True,
        )


async def test_policy_attach_needs_confirm(server, principals):
    with pytest.raises(ToolError, match="AttachUserPolicy refused: this is destructive"):
        await call(server, "iam_policy_attach", **_binding("user", "alice", policy_arn=principals))
    attached = await call(server, "iam_attached_policy_list", **_binding("user", "alice"))
    assert attached["items"] == []


async def test_policy_attach_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="AttachGroupPolicy refused: the server is read-only"):
        await call(
            read_only_server,
            "iam_policy_attach",
            **_binding("group", "readers", policy_arn="arn:aws:iam::1:policy/p"),
            confirm=True,
        )


async def test_attached_policy_list_paginates(stub):
    stub.iam.add_response(
        "list_attached_role_policies",
        {"AttachedPolicies": [], "IsTruncated": True, "Marker": "n"},
        {"RoleName": "reader", "MaxItems": 1, "Marker": "p"},
    )
    result = await call(
        stub.server,
        "iam_attached_policy_list",
        **_binding("role", "reader"),
        max_items=1,
        continuation_token="p",
    )
    assert result == {"items": [], "next_token": "n", "truncated": True}


async def test_attached_policy_list_missing_principal(server):
    with pytest.raises(ToolError, match="ListAttachedGroupPolicies failed: NoSuchEntity"):
        await call(server, "iam_attached_policy_list", **_binding("group", "ghost"))


# iam_policy_detach


@pytest.mark.parametrize(("kind", "name"), PRINCIPALS)
async def test_policy_detach(server, principals, kind, name):
    arn = principals
    await call(server, "iam_policy_attach", **_binding(kind, name, policy_arn=arn), confirm=True)
    result = await call(
        server, "iam_policy_detach", **_binding(kind, name, policy_arn=arn), confirm=True
    )
    assert result["ok"] is True
    attached = await call(server, "iam_attached_policy_list", **_binding(kind, name))
    assert attached["items"] == []


async def test_policy_detach_not_attached(server, principals):
    with pytest.raises(ToolError, match="DetachUserPolicy failed: NoSuchEntity"):
        await call(
            server,
            "iam_policy_detach",
            **_binding("user", "alice", policy_arn=principals),
            confirm=True,
        )


async def test_policy_detach_needs_confirm(server, principals):
    arn = principals
    await call(
        server, "iam_policy_attach", **_binding("user", "alice", policy_arn=arn), confirm=True
    )
    with pytest.raises(ToolError, match="DetachUserPolicy refused: this is destructive"):
        await call(server, "iam_policy_detach", **_binding("user", "alice", policy_arn=arn))
    attached = await call(server, "iam_attached_policy_list", **_binding("user", "alice"))
    assert len(attached["items"]) == 1


async def test_policy_detach_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="DetachUserPolicy refused: the server is read-only"):
        await call(
            read_only_server,
            "iam_policy_detach",
            **_binding("user", "alice", policy_arn="arn:aws:iam::1:policy/p"),
            confirm=True,
        )


# inline policies


@pytest.mark.parametrize(("kind", "name"), PRINCIPALS)
async def test_inline_policy_put_list_get_delete(server, principals, kind, name):
    put = await call(
        server,
        "iam_inline_policy_put",
        **_binding(kind, name, policy_name="inline", policy_document=json.dumps(READ_ONLY_POLICY)),
        confirm=True,
    )
    assert put == {"ok": True, **_binding(kind, name, policy_name="inline")}
    listed = await call(server, "iam_inline_policy_list", **_binding(kind, name))
    assert listed == {"items": ["inline"], "next_token": None, "truncated": False}
    got = await call(server, "iam_inline_policy_get", **_binding(kind, name, policy_name="inline"))
    assert got == {**_binding(kind, name, policy_name="inline"), "document": READ_ONLY_POLICY}
    deleted = await call(
        server,
        "iam_inline_policy_delete",
        **_binding(kind, name, policy_name="inline"),
        confirm=True,
    )
    assert deleted["ok"] is True
    assert (await call(server, "iam_inline_policy_list", **_binding(kind, name)))["items"] == []


async def test_inline_policy_list_paginates(stub):
    stub.iam.add_response(
        "list_user_policies",
        {"PolicyNames": ["a"], "IsTruncated": True, "Marker": "n"},
        {"UserName": "alice", "MaxItems": 1},
    )
    result = await call(
        stub.server, "iam_inline_policy_list", **_binding("user", "alice"), max_items=1
    )
    assert result == {"items": ["a"], "next_token": "n", "truncated": True}


async def test_inline_policy_list_missing_principal(server):
    with pytest.raises(ToolError, match="ListRolePolicies failed: NoSuchEntity"):
        await call(server, "iam_inline_policy_list", **_binding("role", "ghost"))


async def test_inline_policy_get_missing(server, principals):
    with pytest.raises(ToolError, match="GetUserPolicy failed: NoSuchEntity"):
        await call(server, "iam_inline_policy_get", **_binding("user", "alice", policy_name="nope"))


async def test_inline_policy_put_rejects_bad_json(stub):
    with pytest.raises(ToolError, match="policy_document isn't valid JSON"):
        await call(
            stub.server,
            "iam_inline_policy_put",
            **_binding("user", "alice", policy_name="p", policy_document="{nope"),
            confirm=True,
        )


async def test_inline_policy_put_error(stub):
    stub.iam.add_client_error(
        "put_group_policy", **client_error("MalformedPolicyDocument", "bad action", 400)
    )
    with pytest.raises(ToolError, match="PutGroupPolicy failed: MalformedPolicyDocument"):
        await call(
            stub.server,
            "iam_inline_policy_put",
            **_binding("group", "readers", policy_name="p", policy_document=READ_ONLY_POLICY),
            confirm=True,
        )


async def test_inline_policy_put_needs_confirm(server, principals):
    with pytest.raises(ToolError, match="PutUserPolicy refused: this is destructive"):
        await call(
            server,
            "iam_inline_policy_put",
            **_binding("user", "alice", policy_name="p", policy_document=READ_ONLY_POLICY),
        )
    assert (await call(server, "iam_inline_policy_list", **_binding("user", "alice")))[
        "items"
    ] == []


async def test_inline_policy_put_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="PutRolePolicy refused: the server is read-only"):
        await call(
            read_only_server,
            "iam_inline_policy_put",
            **_binding("role", "reader", policy_name="p", policy_document=READ_ONLY_POLICY),
            confirm=True,
        )


async def test_inline_policy_delete_missing(server, principals):
    with pytest.raises(ToolError, match="DeleteUserPolicy failed: NoSuchEntity"):
        await call(
            server,
            "iam_inline_policy_delete",
            **_binding("user", "alice", policy_name="nope"),
            confirm=True,
        )


async def test_inline_policy_delete_needs_confirm(server):
    with pytest.raises(ToolError, match="DeleteUserPolicy refused: this is destructive"):
        await call(server, "iam_inline_policy_delete", **_binding("user", "a", policy_name="p"))


async def test_inline_policy_delete_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="DeleteGroupPolicy refused: the server is read-only"):
        await call(
            read_only_server,
            "iam_inline_policy_delete",
            **_binding("group", "g", policy_name="p"),
            confirm=True,
        )


# iam_policy_validate (Wasabi extension via rest.iam_query)

LIST_BUCKETS_POLICY = {
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Action": "s3:ListAllMyBuckets", "Resource": "*"}],
}
LINT_ONLY_RESPONSE = (
    b'<ValidatePolicyResponse xmlns="https://iam.amazonaws.com/doc/2010-05-08/">'
    b"<ValidatePolicyResult><Messages><member><Severity>WARNING</Severity>"
    b"<Text>Resource is broad</Text></member></Messages></ValidatePolicyResult>"
    b"</ValidatePolicyResponse>"
)
MALFORMED_POLICY = (
    b'<ErrorResponse xmlns="https://iam.amazonaws.com/doc/2010-05-08/">'
    b"<Error><Type>Sender</Type><Code>MalformedPolicy</Code>"
    b"<Message>Missing required field Principal</Message></Error>"
    b"<RequestId>r1</RequestId></ErrorResponse>"
)
# Live Wasabi's 200 body: stray tags follow </ValidatePolicyResponse>, so it isn't XML.
STRAY_TAIL = (
    b"<ResponseMetadata><RequestId>r1</RequestId></ResponseMetadata></CreatePolicyVersionResponse>"
)
LIVE_VALID_RESPONSE = (
    b'<ValidatePolicyResponse xmlns="https://iam.amazonaws.com/doc/2010-05-08/">'
    b"<ValidatePolicyResult></ValidatePolicyResult></ValidatePolicyResponse>" + STRAY_TAIL
)
MALFORMED_POLICY_DOCUMENT = (
    b'<ErrorResponse xmlns="https://iam.amazonaws.com/doc/2010-05-08/">'
    b"<Error><Type>Sender</Type><Code>MalformedPolicyDocument</Code>"
    b"<Message>Syntax errors in policy.</Message>"
    b"<Detail>unexpected end of JSON input</Detail></Error>"
    b"<RequestId>r1</RequestId></ErrorResponse>"
)
OPTIONAL_FIELDS = {"UseAction", "UseResource", "UseService"}


def _live_evaluated_response(evaluation: str) -> bytes:
    return (
        b'<ValidatePolicyResponse xmlns="https://iam.amazonaws.com/doc/2010-05-08/">'
        b"<ValidatePolicyResult><Evaluation>" + evaluation.encode() + b"</Evaluation>"
        b"</ValidatePolicyResult></ValidatePolicyResponse>" + STRAY_TAIL
    )


def _validate_route(content: bytes = VALIDATE_RESPONSE, status: int = 200) -> respx.Route:
    return respx.post(f"{FAKE['iam']}/").mock(return_value=httpx.Response(status, content=content))


def _form(route: respx.Route) -> dict[str, list[str]]:
    return parse_qs(route.calls.last.request.content.decode(), keep_blank_values=True)


@respx.mock
@pytest.mark.parametrize("as_string", [False, True])
async def test_policy_validate_encodes_the_request_as_documented(rest_server, as_string):
    route = _validate_route()
    document = json.dumps(LIST_BUCKETS_POLICY) if as_string else LIST_BUCKETS_POLICY
    await call(rest_server, "iam_policy_validate", policy_document=document)
    request = route.calls.last.request
    assert request.method == "POST"
    assert request.url.path == "/"
    assert not request.url.query
    assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 ")
    assert "/us-east-1/iam/aws4_request" in request.headers["authorization"]
    body = request.content.decode()
    assert "+" not in body
    form = _form(route)
    assert form["Action"] == ["ValidatePolicy"]
    assert form["Version"] == ["2010-05-08"]
    # One form decode plus one JSON decode gives the policy back: not double-encoded.
    [policy] = form["PolicyDocument"]
    assert json.loads(policy) == LIST_BUCKETS_POLICY
    assert policy.startswith("{")


@respx.mock
@pytest.mark.parametrize("explicit", [False, True])
async def test_policy_validate_omits_is_principal_for_an_identity_policy(rest_server, explicit):
    route = _validate_route()
    extra = {"is_principal": True} if explicit else {}
    await call(rest_server, "iam_policy_validate", policy_document=LIST_BUCKETS_POLICY, **extra)
    assert "IsPrincipal" not in _form(route)


@respx.mock
async def test_policy_validate_sends_is_principal_false_for_a_resource_policy(rest_server):
    route = _validate_route()
    await call(rest_server, "iam_policy_validate", policy_document=TRUST, is_principal=False)
    assert _form(route)["IsPrincipal"] == ["false"]


@respx.mock
async def test_policy_validate_sends_evaluation_fields_when_set(rest_server):
    route = _validate_route()
    await call(
        rest_server,
        "iam_policy_validate",
        policy_document=READ_ONLY_POLICY,
        use_action="s3:GetObject",
        use_resource="arn:aws:s3:::bucket-x/a.txt",
        use_service="s3",
    )
    form = _form(route)
    assert form["UseAction"] == ["s3:GetObject"]
    assert form["UseResource"] == ["arn:aws:s3:::bucket-x/a.txt"]
    assert form["UseService"] == ["s3"]


@respx.mock
async def test_policy_validate_omits_evaluation_fields_when_not_set(rest_server):
    route = _validate_route(LINT_ONLY_RESPONSE)
    await call(rest_server, "iam_policy_validate", policy_document=READ_ONLY_POLICY)
    form = _form(route)
    assert set(form) == {"Action", "Version", "PolicyDocument"}
    assert not OPTIONAL_FIELDS & set(form)


@respx.mock
async def test_policy_validate_returns_the_evaluation_and_lint_result(rest_server):
    _validate_route()
    result = await call(
        rest_server,
        "iam_policy_validate",
        policy_document=LIST_BUCKETS_POLICY,
        use_action="s3:ListAllMyBuckets",
        use_resource="*",
        use_service="s3",
    )
    assert result == {
        "validation": {
            "Result": "ALLOW",
            "Messages": [{"Severity": "WARNING", "Text": "Resource is broad"}],
        }
    }
    assert_no_secrets(result)


@respx.mock
async def test_policy_validate_returns_a_lint_only_result(rest_server):
    _validate_route(LINT_ONLY_RESPONSE)
    result = await call(rest_server, "iam_policy_validate", policy_document=LIST_BUCKETS_POLICY)
    assert result == {
        "validation": {"Messages": [{"Severity": "WARNING", "Text": "Resource is broad"}]}
    }


@respx.mock
async def test_policy_validate_returns_an_empty_result(rest_server):
    _validate_route(b"<ValidatePolicyResponse><ValidatePolicyResult/></ValidatePolicyResponse>")
    result = await call(rest_server, "iam_policy_validate", policy_document=LIST_BUCKETS_POLICY)
    assert result == {"validation": {}}


@respx.mock
@pytest.mark.parametrize("status", [400, 200])
async def test_policy_validate_reports_wasabi_malformed_policy(rest_server, status):
    _validate_route(MALFORMED_POLICY, status)
    with pytest.raises(
        ToolError,
        match="ValidatePolicy failed: MalformedPolicy: Missing required field Principal",
    ):
        await call(rest_server, "iam_policy_validate", policy_document=LIST_BUCKETS_POLICY)


@respx.mock
async def test_policy_validate_reports_wasabi_error(rest_server):
    _validate_route(IAM_ERROR, 400)
    with pytest.raises(ToolError, match="ValidatePolicy failed: MalformedPolicyDocument"):
        await call(rest_server, "iam_policy_validate", policy_document=READ_ONLY_POLICY)


@respx.mock(assert_all_called=False)
async def test_policy_validate_rejects_bad_json_without_calling(rest_server):
    route = respx.post(f"{FAKE['iam']}/").mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match="policy_document isn't valid JSON"):
        await call(rest_server, "iam_policy_validate", policy_document="{bad")
    assert not route.called


@respx.mock
async def test_policy_validate_parses_wasabis_malformed_success_body(rest_server):
    _validate_route(LIVE_VALID_RESPONSE)
    result = await call(rest_server, "iam_policy_validate", policy_document=LIST_BUCKETS_POLICY)
    assert result == {"validation": {}}


@respx.mock
@pytest.mark.parametrize("evaluation", ["Allow", "Deny"])
async def test_policy_validate_returns_wasabis_evaluation(rest_server, evaluation):
    _validate_route(_live_evaluated_response(evaluation))
    result = await call(
        rest_server,
        "iam_policy_validate",
        policy_document=LIST_BUCKETS_POLICY,
        use_action="s3:ListAllMyBuckets",
        use_resource="*",
        use_service="s3",
    )
    assert result["evaluation"] == evaluation
    assert result["validation"] == {"Evaluation": evaluation}


@respx.mock
async def test_policy_validate_reports_the_error_detail(rest_server):
    _validate_route(MALFORMED_POLICY_DOCUMENT, 400)
    with pytest.raises(ToolError) as exc:
        await call(rest_server, "iam_policy_validate", policy_document=LIST_BUCKETS_POLICY)
    assert (
        "ValidatePolicy failed: MalformedPolicyDocument: Syntax errors in policy. "
        "(unexpected end of JSON input)"
    ) in str(exc.value)


@respx.mock
async def test_policy_validate_refuses_an_unparseable_body(rest_server):
    _validate_route(b"<Unexpected><ResponseMetadata></Unexpected>")
    with pytest.raises(ToolError, match="ValidatePolicy failed: the response wasn't valid XML"):
        await call(rest_server, "iam_policy_validate", policy_document=LIST_BUCKETS_POLICY)

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


@respx.mock
async def test_policy_validate_sends_signed_form_post(rest_server):
    route = respx.post(f"{FAKE['iam']}/").mock(
        return_value=httpx.Response(200, content=VALIDATE_RESPONSE)
    )
    result = await call(
        rest_server,
        "iam_policy_validate",
        policy_document=READ_ONLY_POLICY,
        is_principal=True,
        use_action="s3:GetObject",
        use_resource="arn:aws:s3:::bucket-x/a.txt",
        use_service="s3",
    )
    assert result == {
        "validation": {
            "Result": "ALLOW",
            "Messages": [{"Severity": "WARNING", "Text": "Resource is broad"}],
        }
    }
    request = route.calls.last.request
    assert request.method == "POST"
    assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 ")
    assert "/us-east-1/iam/aws4_request" in request.headers["authorization"]
    form = parse_qs(request.content.decode())
    assert form["Action"] == ["ValidatePolicy"]
    assert form["Version"] == ["2010-05-08"]
    assert json.loads(form["PolicyDocument"][0]) == READ_ONLY_POLICY
    assert form["IsPrincipal"] == ["true"]
    assert form["UseAction"] == ["s3:GetObject"]
    assert form["UseResource"] == ["arn:aws:s3:::bucket-x/a.txt"]
    assert form["UseService"] == ["s3"]
    assert_no_secrets(result)


@respx.mock
async def test_policy_validate_lint_only_omits_optional_fields(rest_server):
    route = respx.post(f"{FAKE['iam']}/").mock(
        return_value=httpx.Response(
            200,
            content=b"<ValidatePolicyResponse><ValidatePolicyResult/></ValidatePolicyResponse>",
        )
    )
    result = await call(
        rest_server,
        "iam_policy_validate",
        policy_document=json.dumps(READ_ONLY_POLICY),
        is_principal=False,
    )
    assert result == {"validation": {}}
    form = parse_qs(route.calls.last.request.content.decode())
    assert set(form) == {"Action", "Version", "PolicyDocument", "IsPrincipal"}
    assert form["IsPrincipal"] == ["false"]


@respx.mock
async def test_policy_validate_reports_wasabi_error(rest_server):
    respx.post(f"{FAKE['iam']}/").mock(return_value=httpx.Response(400, content=IAM_ERROR))
    with pytest.raises(ToolError, match="ValidatePolicy failed: MalformedPolicyDocument"):
        await call(rest_server, "iam_policy_validate", policy_document=READ_ONLY_POLICY)


@respx.mock(assert_all_called=False)
async def test_policy_validate_rejects_bad_json_without_calling(rest_server):
    route = respx.post(f"{FAKE['iam']}/").mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match="policy_document isn't valid JSON"):
        await call(rest_server, "iam_policy_validate", policy_document="{bad")
    assert not route.called

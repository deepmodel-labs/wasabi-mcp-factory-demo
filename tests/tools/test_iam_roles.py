import json

import pytest

from tests.conftest import FAKE_ACCESS_KEY
from tests.helpers import ToolError, call
from tests.tools.iam_support import READ_ONLY_POLICY, assert_no_secrets, client_error, stubbed
from wasabi_mcp.clients import Clients

TRUST = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"AWS": "arn:aws:iam::123456789012:user/alice"},
            "Action": "sts:AssumeRole",
        }
    ],
}
ROLE_ARN = "arn:aws:iam::123456789012:role/reader"


@pytest.fixture
def iam(settings):
    """A boto3 IAM client on moto, for setup the tools under test don't cover."""
    return Clients(settings).iam()


@pytest.fixture
def stub(rest_settings, monkeypatch):
    yield from stubbed(rest_settings, monkeypatch)


async def _role(server, name="reader", **extra):
    return await call(
        server, "iam_role_create", role_name=name, assume_role_policy=TRUST, confirm=True, **extra
    )


# iam_role_list


async def test_role_list(server):
    await _role(server)
    result = await call(server, "iam_role_list")
    [role] = result["items"]
    assert role["RoleName"] == "reader"
    assert role["AssumeRolePolicyDocument"] == TRUST
    assert result["truncated"] is False and result["next_token"] is None


async def test_role_list_paginates(stub):
    stub.iam.add_response(
        "list_roles",
        {"Roles": [], "IsTruncated": True, "Marker": "next"},
        {"PathPrefix": "/svc/", "MaxItems": 3, "Marker": "prev"},
    )
    result = await call(
        stub.server, "iam_role_list", path_prefix="/svc/", max_items=3, continuation_token="prev"
    )
    assert result == {"items": [], "next_token": "next", "truncated": True}


# iam_role_get


async def test_role_get(server, iam):
    await _role(server)
    arn = iam.create_policy(PolicyName="ro", PolicyDocument=json.dumps(READ_ONLY_POLICY))["Policy"][
        "Arn"
    ]
    iam.attach_role_policy(RoleName="reader", PolicyArn=arn)
    iam.put_role_policy(
        RoleName="reader", PolicyName="inline", PolicyDocument=json.dumps(READ_ONLY_POLICY)
    )
    result = await call(server, "iam_role_get", role_name="reader")
    assert result["role"]["RoleName"] == "reader"
    assert result["role"]["AssumeRolePolicyDocument"] == TRUST
    assert result["attached_policies"] == [{"PolicyName": "ro", "PolicyArn": arn}]
    assert result["inline_policies"] == ["inline"]


async def test_role_get_missing(server):
    with pytest.raises(ToolError, match="GetRole failed: NoSuchEntity"):
        await call(server, "iam_role_get", role_name="ghost")


# iam_role_create


async def test_role_create(server):
    result = await _role(server, description="read only", max_session_duration=7200, path="/svc/")
    role = result["role"]
    assert result["ok"] is True
    assert role["RoleName"] == "reader"
    assert role["MaxSessionDuration"] == 7200
    assert role["Arn"].endswith(":role/svc/reader")
    assert role["AssumeRolePolicyDocument"] == TRUST


async def test_role_create_accepts_json_string(server):
    result = await call(
        server,
        "iam_role_create",
        role_name="reader",
        assume_role_policy=json.dumps(TRUST),
        confirm=True,
    )
    assert result["role"]["AssumeRolePolicyDocument"] == TRUST


async def test_role_create_rejects_bad_json_and_duration(stub):
    # Nothing is stubbed, so either call reaching IAM would fail the Stubber.
    with pytest.raises(ToolError, match="assume_role_policy isn't valid JSON"):
        await call(
            stub.server,
            "iam_role_create",
            role_name="r",
            assume_role_policy="{bad",
            confirm=True,
        )
    with pytest.raises(ToolError, match="max_session_duration must be between 3600 and 43200"):
        await call(
            stub.server,
            "iam_role_create",
            role_name="r",
            assume_role_policy=TRUST,
            max_session_duration=50_000,
            confirm=True,
        )


async def test_role_create_duplicate(server):
    await _role(server)
    with pytest.raises(ToolError, match="CreateRole failed: EntityAlreadyExists"):
        await _role(server)


async def test_role_create_needs_confirm(server):
    with pytest.raises(ToolError, match="CreateRole refused: this is destructive"):
        await call(server, "iam_role_create", role_name="reader", assume_role_policy=TRUST)
    assert (await call(server, "iam_role_list"))["items"] == []


async def test_role_create_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="CreateRole refused: the server is read-only"):
        await _role(read_only_server)


# iam_role_delete


async def test_role_delete(server):
    await _role(server)
    result = await call(server, "iam_role_delete", role_name="reader", confirm=True)
    assert result == {"ok": True, "role_name": "reader", "steps": ["DeleteRole"]}
    assert (await call(server, "iam_role_list"))["items"] == []


async def test_role_delete_with_policies_explains_cascade(server, iam):
    await _role(server)
    arn = iam.create_policy(PolicyName="ro", PolicyDocument=json.dumps(READ_ONLY_POLICY))["Policy"][
        "Arn"
    ]
    iam.attach_role_policy(RoleName="reader", PolicyArn=arn)
    with pytest.raises(ToolError, match="DeleteConflict.*cascade=true"):
        await call(server, "iam_role_delete", role_name="reader", confirm=True)


async def test_role_delete_cascade(server, iam):
    await _role(server)
    arn = iam.create_policy(PolicyName="ro", PolicyDocument=json.dumps(READ_ONLY_POLICY))["Policy"][
        "Arn"
    ]
    iam.attach_role_policy(RoleName="reader", PolicyArn=arn)
    iam.put_role_policy(
        RoleName="reader", PolicyName="inline", PolicyDocument=json.dumps(READ_ONLY_POLICY)
    )
    result = await call(server, "iam_role_delete", role_name="reader", cascade=True, confirm=True)
    assert result["steps"] == [f"DetachRolePolicy {arn}", "DeleteRolePolicy inline", "DeleteRole"]


async def test_role_delete_needs_confirm(server):
    await _role(server)
    with pytest.raises(ToolError, match="DeleteRole refused: this is destructive"):
        await call(server, "iam_role_delete", role_name="reader")
    assert len((await call(server, "iam_role_list"))["items"]) == 1


async def test_role_delete_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="DeleteRole refused: the server is read-only"):
        await call(read_only_server, "iam_role_delete", role_name="reader", confirm=True)


# iam_role_trust_policy_update


async def test_role_trust_policy_update(server):
    await _role(server)
    new_trust = {
        **TRUST,
        "Statement": [{**TRUST["Statement"][0], "Principal": {"AWS": "arn:aws:iam::1:root"}}],
    }
    result = await call(
        server,
        "iam_role_trust_policy_update",
        role_name="reader",
        policy_document=new_trust,
        confirm=True,
    )
    assert result == {"ok": True, "role_name": "reader"}
    role = (await call(server, "iam_role_get", role_name="reader"))["role"]
    assert role["AssumeRolePolicyDocument"] == new_trust


async def test_role_trust_policy_update_missing_role(server):
    with pytest.raises(ToolError, match="UpdateAssumeRolePolicy failed: NoSuchEntity"):
        await call(
            server,
            "iam_role_trust_policy_update",
            role_name="ghost",
            policy_document=TRUST,
            confirm=True,
        )


async def test_role_trust_policy_update_needs_confirm(server):
    with pytest.raises(ToolError, match="UpdateAssumeRolePolicy refused: this is destructive"):
        await call(
            server, "iam_role_trust_policy_update", role_name="reader", policy_document=TRUST
        )


async def test_role_trust_policy_update_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="UpdateAssumeRolePolicy refused: the server is read-only"):
        await call(
            read_only_server,
            "iam_role_trust_policy_update",
            role_name="reader",
            policy_document=TRUST,
            confirm=True,
        )


# sts_caller_identity


async def test_caller_identity(server):
    result = await call(server, "sts_caller_identity")
    assert set(result) == {"account", "user_id", "arn"}
    assert result["account"] == "123456789012"
    assert result["arn"].startswith("arn:aws:sts::123456789012:")
    assert_no_secrets(result, FAKE_ACCESS_KEY)


async def test_caller_identity_withholds_configured_key_id(stub):
    stub.sts.add_response(
        "get_caller_identity",
        {
            "UserId": FAKE_ACCESS_KEY,
            "Account": "100000000001",
            "Arn": "arn:aws:iam::100000000001:root",
        },
        {},
    )
    result = await call(stub.server, "sts_caller_identity")
    assert result == {
        "account": "100000000001",
        "user_id": None,
        "arn": "arn:aws:iam::100000000001:root",
    }


async def test_caller_identity_error(stub):
    stub.sts.add_client_error(
        "get_caller_identity", **client_error("InvalidClientTokenId", "bad key", 403)
    )
    with pytest.raises(ToolError, match="GetCallerIdentity failed: InvalidClientTokenId"):
        await call(stub.server, "sts_caller_identity")


# sts_assume_role


async def test_assume_role_returns_credentials_once(server):
    await _role(server)
    result = await call(
        server,
        "sts_assume_role",
        role_arn=ROLE_ARN,
        session_name="contractor",
        duration_seconds=900,
        policy=READ_ONLY_POLICY,
        confirm=True,
    )
    credentials = result["credentials"]
    assert set(credentials) == {"access_key_id", "secret_access_key", "session_token", "expiration"}
    assert credentials["secret_access_key"] and credentials["session_token"]
    assert result["assumed_role_user"]["Arn"].endswith("assumed-role/reader/contractor")
    assert "only once" in result["note"]
    identity = await call(server, "sts_caller_identity")
    role = await call(server, "iam_role_get", role_name="reader")
    assert_no_secrets([identity, role], credentials["secret_access_key"])


async def test_assume_role_as_root_explains(stub):
    stub.sts.add_client_error(
        "assume_role", **client_error("AccessDenied", "Roles may not be assumed by root", 403)
    )
    with pytest.raises(ToolError, match="AssumeRole failed: AccessDenied.*root account"):
        await call(
            stub.server, "sts_assume_role", role_arn=ROLE_ARN, session_name="s1", confirm=True
        )


async def test_assume_role_validates_duration_before_calling(stub):
    with pytest.raises(ToolError, match="duration_seconds must be between 900 and 43200"):
        await call(
            stub.server,
            "sts_assume_role",
            role_arn=ROLE_ARN,
            session_name="s1",
            duration_seconds=43_201,
            confirm=True,
        )


async def test_assume_role_needs_confirm(stub):
    with pytest.raises(ToolError, match="AssumeRole refused: this is destructive"):
        await call(stub.server, "sts_assume_role", role_arn=ROLE_ARN, session_name="s1")


async def test_assume_role_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="AssumeRole refused: the server is read-only"):
        await call(
            read_only_server,
            "sts_assume_role",
            role_arn=ROLE_ARN,
            session_name="s1",
            confirm=True,
        )


# sts_session_token


async def test_session_token_returns_credentials_once(server):
    result = await call(server, "sts_session_token", duration_seconds=900, confirm=True)
    assert set(result["credentials"]) == {
        "access_key_id",
        "secret_access_key",
        "session_token",
        "expiration",
    }
    assert "only once" in result["note"]


async def test_session_token_with_mfa(stub):
    stub.sts.add_response(
        "get_session_token",
        {
            "Credentials": {
                "AccessKeyId": "ASIATEMP000000000000",
                "SecretAccessKey": "temp-secret",
                "SessionToken": "temp-token",
                "Expiration": "2026-11-20T12:00:00Z",
            }
        },
        {"SerialNumber": "arn:aws:iam::1:mfa/alice", "TokenCode": "123456"},
    )
    result = await call(
        stub.server,
        "sts_session_token",
        mfa_serial="arn:aws:iam::1:mfa/alice",
        mfa_code="123456",
        confirm=True,
    )
    assert result["credentials"]["secret_access_key"] == "temp-secret"
    assert result["credentials"]["expiration"].startswith("2026-11-20T12:00:00")


async def test_session_token_needs_both_mfa_fields(stub):
    with pytest.raises(ToolError, match="both mfa_serial and mfa_code"):
        await call(stub.server, "sts_session_token", mfa_serial="arn:x", confirm=True)


async def test_session_token_validates_duration(stub):
    with pytest.raises(ToolError, match="duration_seconds must be between 900 and 43200"):
        await call(stub.server, "sts_session_token", duration_seconds=60, confirm=True)


async def test_session_token_error(stub):
    stub.sts.add_client_error(
        "get_session_token", **client_error("AccessDenied", "MFA failed", 403)
    )
    with pytest.raises(ToolError, match="GetSessionToken failed: AccessDenied: MFA failed"):
        await call(stub.server, "sts_session_token", confirm=True)


async def test_session_token_needs_confirm(stub):
    with pytest.raises(ToolError, match="GetSessionToken refused: this is destructive"):
        await call(stub.server, "sts_session_token")


async def test_session_token_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="GetSessionToken refused: the server is read-only"):
        await call(read_only_server, "sts_session_token", confirm=True)

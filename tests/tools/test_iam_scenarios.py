"""PRD scenarios S3 (onboard a read-only contractor) and S8 (remove a user's access)."""

import json

import pytest

from tests.conftest import SECRETS
from tests.helpers import ToolError, call
from tests.tools.iam_support import READ_ONLY_POLICY, assert_no_secrets


async def test_s3_onboard_read_only_contractor(server):
    outputs = {}
    outputs["policy"] = await call(
        server,
        "iam_policy_create",
        policy_name="bucket-x-read-only",
        policy_document=READ_ONLY_POLICY,
        confirm=True,
    )
    policy_arn = outputs["policy"]["policy"]["Arn"]
    outputs["group"] = await call(server, "iam_group_create", group_name="contractors")
    outputs["user"] = await call(server, "iam_user_create", user_name="contractor")
    outputs["membership"] = await call(
        server,
        "iam_group_add_user",
        group_name="contractors",
        user_name="contractor",
        confirm=True,
    )
    outputs["attach"] = await call(
        server,
        "iam_policy_attach",
        principal_type="group",
        principal_name="contractors",
        policy_arn=policy_arn,
        confirm=True,
    )
    key = await call(server, "iam_access_key_create", user_name="contractor", confirm=True)
    secret = key["secret_access_key"]
    assert secret and key["access_key_id"]

    # Reads after onboarding show the setup, never the secret.
    outputs["user_get"] = await call(server, "iam_user_get", user_name="contractor")
    outputs["group_get"] = await call(server, "iam_group_get", group_name="contractors")
    outputs["keys"] = await call(server, "iam_access_key_list", user_name="contractor")
    outputs["policy_get"] = await call(server, "iam_policy_get", policy_arn=policy_arn)

    assert outputs["user_get"]["groups"] == ["contractors"]
    assert outputs["group_get"]["members"] == ["contractor"]
    assert outputs["group_get"]["attached_policies"] == [
        {"PolicyName": "bucket-x-read-only", "PolicyArn": policy_arn}
    ]
    assert outputs["policy_get"]["document"] == READ_ONLY_POLICY
    assert [k["AccessKeyId"] for k in outputs["keys"]["items"]] == [key["access_key_id"]]

    # The secret appears only in the confirmed iam_access_key_create response.
    assert secret in json.dumps(key)
    for name, output in outputs.items():
        assert secret not in json.dumps(output), name
    assert_no_secrets(outputs, secret)
    for configured in SECRETS:
        assert configured not in json.dumps(key)


async def test_s3_access_key_create_refuses_without_confirm(server):
    await call(server, "iam_user_create", user_name="contractor")
    with pytest.raises(ToolError, match="CreateAccessKey refused: this is destructive"):
        await call(server, "iam_access_key_create", user_name="contractor")
    keys = await call(server, "iam_access_key_list", user_name="contractor")
    assert keys["items"] == []


@pytest.fixture
async def user_with_access(server):
    """User ``leaver`` in group ``staff``, with a policy attached directly and one key."""
    policy = await call(
        server,
        "iam_policy_create",
        policy_name="bucket-x-read-only",
        policy_document=READ_ONLY_POLICY,
        confirm=True,
    )
    arn = policy["policy"]["Arn"]
    await call(server, "iam_group_create", group_name="staff")
    await call(server, "iam_user_create", user_name="leaver")
    await call(server, "iam_group_add_user", group_name="staff", user_name="leaver", confirm=True)
    await call(
        server,
        "iam_policy_attach",
        principal_type="user",
        principal_name="leaver",
        policy_arn=arn,
        confirm=True,
    )
    key = await call(server, "iam_access_key_create", user_name="leaver", confirm=True)
    return {"policy_arn": arn, "access_key_id": key["access_key_id"]}


def _s8_calls(access):
    return [
        ("iam_group_remove_user", {"group_name": "staff", "user_name": "leaver"}),
        (
            "iam_policy_detach",
            {
                "principal_type": "user",
                "principal_name": "leaver",
                "policy_arn": access["policy_arn"],
            },
        ),
        (
            "iam_access_key_update",
            {"user_name": "leaver", "access_key_id": access["access_key_id"], "active": False},
        ),
        (
            "iam_access_key_delete",
            {"user_name": "leaver", "access_key_id": access["access_key_id"]},
        ),
    ]


async def test_s8_tools_refuse_in_read_only_mode(server, read_only_server, user_with_access):
    before = await call(read_only_server, "iam_user_get", user_name="leaver")
    for name, arguments in _s8_calls(user_with_access):
        with pytest.raises(ToolError, match="refused: the server is read-only"):
            await call(read_only_server, name, **arguments, confirm=True)
    assert await call(read_only_server, "iam_user_get", user_name="leaver") == before


async def test_s8_destructive_tools_need_confirm(server, user_with_access):
    before = await call(server, "iam_user_get", user_name="leaver")
    for name, arguments in _s8_calls(user_with_access):
        with pytest.raises(ToolError, match="refused: this is destructive"):
            await call(server, name, **arguments)
        with pytest.raises(ToolError, match="refused: this is destructive"):
            await call(server, name, **arguments, confirm=False)
    assert await call(server, "iam_user_get", user_name="leaver") == before


async def test_s8_remove_user_access_everywhere(server, user_with_access):
    summary = await call(server, "iam_user_get", user_name="leaver")
    assert summary["groups"] == ["staff"]
    assert [p["PolicyArn"] for p in summary["attached_policies"]] == [
        user_with_access["policy_arn"]
    ]
    for name, arguments in _s8_calls(user_with_access):
        result = await call(server, name, **arguments, confirm=True)
        assert result["ok"] is True, name
    after = await call(server, "iam_user_get", user_name="leaver")
    assert after["groups"] == []
    assert after["attached_policies"] == []
    assert after["access_keys"] == []
    assert_no_secrets(after)

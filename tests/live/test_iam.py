"""Live IAM user, group, policy, attach and delete flow (PRD section 8.4) and
ValidatePolicy (PRD section 6.12)."""

from __future__ import annotations

import json

import pytest

from tests.helpers import ToolError, call
from tests.live.support import read_only_policy

pytestmark = pytest.mark.live


async def test_iam_user_group_policy_attach_and_delete(live_server, new_name):
    user, group, policy_name = new_name("user"), new_name("group"), new_name("policy")

    await call(live_server, "iam_user_create", user_name=user)
    await call(live_server, "iam_group_create", group_name=group)
    created = await call(
        live_server,
        "iam_policy_create",
        policy_name=policy_name,
        policy_document=read_only_policy("wasabi-mcp-test-unused"),
        confirm=True,
    )
    arn = created["policy"]["Arn"]

    await call(live_server, "iam_group_add_user", group_name=group, user_name=user, confirm=True)
    for principal_type, principal_name in (("group", group), ("user", user)):
        await call(
            live_server,
            "iam_policy_attach",
            principal_type=principal_type,
            principal_name=principal_name,
            policy_arn=arn,
            confirm=True,
        )

    user_info = await call(live_server, "iam_user_get", user_name=user)
    assert user_info["groups"] == [group]
    assert [p["PolicyArn"] for p in user_info["attached_policies"]] == [arn]
    group_info = await call(live_server, "iam_group_get", group_name=group)
    assert group_info["members"] == [user]
    assert [p["PolicyArn"] for p in group_info["attached_policies"]] == [arn]

    for principal_type, principal_name in (("group", group), ("user", user)):
        await call(
            live_server,
            "iam_policy_detach",
            principal_type=principal_type,
            principal_name=principal_name,
            policy_arn=arn,
            confirm=True,
        )
    await call(live_server, "iam_group_remove_user", group_name=group, user_name=user, confirm=True)
    await call(live_server, "iam_policy_delete", policy_arn=arn, confirm=True)
    await call(live_server, "iam_group_delete", group_name=group, confirm=True)
    await call(live_server, "iam_user_delete", user_name=user, confirm=True)

    with pytest.raises(ToolError, match="NoSuchEntity"):
        await call(live_server, "iam_user_get", user_name=user)
    with pytest.raises(ToolError, match="NoSuchEntity"):
        await call(live_server, "iam_group_get", group_name=group)
    with pytest.raises(ToolError, match="NoSuchEntity"):
        await call(live_server, "iam_policy_get", policy_arn=arn)


LIST_BUCKETS_POLICY = {
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Action": "s3:ListAllMyBuckets", "Resource": "*"}],
}


async def test_iam_policy_validate_identity_policy(live_server):
    # The default is_principal=true lints it as an identity policy: no Principal needed.
    linted = await call(live_server, "iam_policy_validate", policy_document=LIST_BUCKETS_POLICY)
    assert "validation" in linted

    evaluated = await call(
        live_server,
        "iam_policy_validate",
        policy_document=LIST_BUCKETS_POLICY,
        use_action="s3:ListAllMyBuckets",
        use_resource="*",
        use_service="s3",
    )
    # Wasabi's exact response shape is unconfirmed, so look for ALLOW anywhere in it.
    assert "ALLOW" in json.dumps(evaluated["validation"])

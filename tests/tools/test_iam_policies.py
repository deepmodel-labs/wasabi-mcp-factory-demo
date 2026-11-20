import pytest

from tests.helpers import ToolError, call
from tests.tools.iam_support import READ_ONLY_POLICY, assert_no_secrets, client_error, stubbed

WRITE_POLICY = {
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Action": "s3:PutObject", "Resource": "*"}],
}
POLICY_ARN = "arn:aws:iam::123456789012:policy/ro"


@pytest.fixture
def stub(rest_settings, monkeypatch):
    yield from stubbed(rest_settings, monkeypatch)


async def _policy(server, name="ro", document=READ_ONLY_POLICY):
    result = await call(
        server, "iam_policy_create", policy_name=name, policy_document=document, confirm=True
    )
    return result["policy"]["Arn"]


async def _version(server, arn, default=False):
    return await call(
        server,
        "iam_policy_version_create",
        policy_arn=arn,
        policy_document=WRITE_POLICY,
        set_as_default=default,
        confirm=True,
    )


# iam_policy_list


async def test_policy_list_local(server):
    arn = await _policy(server)
    result = await call(server, "iam_policy_list", scope="Local")
    assert [p["Arn"] for p in result["items"]] == [arn]
    assert result["truncated"] is False
    assert_no_secrets(result)


async def test_policy_list_defaults_to_all_and_maps_params(stub):
    stub.iam.add_response(
        "list_policies",
        {"Policies": [{"PolicyName": "WasabiReadOnlyAccess"}], "IsTruncated": True, "Marker": "n"},
        {"Scope": "All", "OnlyAttached": True, "MaxItems": 50, "Marker": "p"},
    )
    result = await call(
        stub.server, "iam_policy_list", only_attached=True, max_items=50, continuation_token="p"
    )
    assert result == {
        "items": [{"PolicyName": "WasabiReadOnlyAccess"}],
        "next_token": "n",
        "truncated": True,
    }


async def test_policy_list_rejects_unknown_scope(server):
    with pytest.raises(ToolError):
        await call(server, "iam_policy_list", scope="Everything")


# iam_policy_get


async def test_policy_get_includes_document(server):
    arn = await _policy(server)
    result = await call(server, "iam_policy_get", policy_arn=arn)
    assert result["policy"]["Arn"] == arn
    assert result["policy"]["DefaultVersionId"] == "v1"
    assert result["document"] == READ_ONLY_POLICY


async def test_policy_get_missing(server):
    with pytest.raises(ToolError, match="GetPolicy failed: NoSuchEntity"):
        await call(server, "iam_policy_get", policy_arn=POLICY_ARN)


# iam_policy_create


async def test_policy_create(server):
    result = await call(
        server,
        "iam_policy_create",
        policy_name="ro",
        policy_document=READ_ONLY_POLICY,
        description="read bucket-x",
        path="/team/",
        confirm=True,
    )
    assert result["ok"] is True
    assert result["policy"]["Arn"].endswith(":policy/team/ro")


async def test_policy_create_rejects_bad_json(stub):
    with pytest.raises(ToolError, match="policy_document isn't valid JSON"):
        await call(
            stub.server,
            "iam_policy_create",
            policy_name="ro",
            policy_document="{oops",
            confirm=True,
        )


async def test_policy_create_duplicate(server):
    await _policy(server)
    with pytest.raises(ToolError, match="CreatePolicy failed: EntityAlreadyExists"):
        await _policy(server)


async def test_policy_create_needs_confirm(server):
    with pytest.raises(ToolError, match="CreatePolicy refused: this is destructive"):
        await call(server, "iam_policy_create", policy_name="ro", policy_document=READ_ONLY_POLICY)
    assert (await call(server, "iam_policy_list", scope="Local"))["items"] == []


async def test_policy_create_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="CreatePolicy refused: the server is read-only"):
        await _policy(read_only_server)


# iam_policy_delete


async def test_policy_delete(server):
    arn = await _policy(server)
    result = await call(server, "iam_policy_delete", policy_arn=arn, confirm=True)
    assert result == {"ok": True, "policy_arn": arn, "steps": ["DeletePolicy"]}
    assert (await call(server, "iam_policy_list", scope="Local"))["items"] == []


async def test_policy_delete_cascade(server):
    arn = await _policy(server)
    await _version(server, arn)
    await call(server, "iam_user_create", user_name="alice")
    await call(server, "iam_group_create", group_name="readers")
    for kind, name in (("user", "alice"), ("group", "readers")):
        await call(
            server,
            "iam_policy_attach",
            principal_type=kind,
            principal_name=name,
            policy_arn=arn,
            confirm=True,
        )
    result = await call(server, "iam_policy_delete", policy_arn=arn, cascade=True, confirm=True)
    assert result["steps"] == [
        "DetachUserPolicy alice",
        "DetachGroupPolicy readers",
        "DeletePolicyVersion v2",
        "DeletePolicy",
    ]
    attached = await call(
        server, "iam_attached_policy_list", principal_type="user", principal_name="alice"
    )
    assert attached["items"] == []


async def test_policy_delete_conflict_explains_cascade(stub):
    stub.iam.add_client_error(
        "delete_policy", **client_error("DeleteConflict", "Cannot delete a policy attached")
    )
    with pytest.raises(ToolError, match="DeleteConflict.*cascade=true"):
        await call(stub.server, "iam_policy_delete", policy_arn=POLICY_ARN, confirm=True)


async def test_policy_delete_needs_confirm(server):
    arn = await _policy(server)
    with pytest.raises(ToolError, match="DeletePolicy refused: this is destructive"):
        await call(server, "iam_policy_delete", policy_arn=arn)
    assert len((await call(server, "iam_policy_list", scope="Local"))["items"]) == 1


async def test_policy_delete_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="DeletePolicy refused: the server is read-only"):
        await call(read_only_server, "iam_policy_delete", policy_arn=POLICY_ARN, confirm=True)


# iam_policy_version_list


async def test_policy_version_list(server):
    arn = await _policy(server)
    await _version(server, arn)
    result = await call(server, "iam_policy_version_list", policy_arn=arn)
    assert [(v["VersionId"], v["IsDefaultVersion"]) for v in result["items"]] == [
        ("v1", True),
        ("v2", False),
    ]
    assert result["truncated"] is False


async def test_policy_version_list_missing_policy(server):
    with pytest.raises(ToolError, match="ListPolicyVersions failed: NoSuchEntity"):
        await call(server, "iam_policy_version_list", policy_arn=POLICY_ARN)


# iam_policy_version_get


async def test_policy_version_get(server):
    arn = await _policy(server)
    await _version(server, arn)
    result = await call(server, "iam_policy_version_get", policy_arn=arn, version_id="v2")
    assert result["VersionId"] == "v2"
    assert result["Document"] == WRITE_POLICY


async def test_policy_version_get_decodes_url_encoded_document(stub):
    stub.iam.add_response(
        "get_policy_version",
        {
            "PolicyVersion": {
                "VersionId": "v1",
                "Document": "%7B%22Version%22%3A%222012-10-17%22%7D",
            }
        },
        {"PolicyArn": POLICY_ARN, "VersionId": "v1"},
    )
    result = await call(
        stub.server, "iam_policy_version_get", policy_arn=POLICY_ARN, version_id="v1"
    )
    assert result["Document"] == {"Version": "2012-10-17"}


async def test_policy_version_get_missing(server):
    arn = await _policy(server)
    with pytest.raises(ToolError, match="GetPolicyVersion failed: NoSuchEntity"):
        await call(server, "iam_policy_version_get", policy_arn=arn, version_id="v9")


# iam_policy_version_create


async def test_policy_version_create_as_default(server):
    arn = await _policy(server)
    result = await _version(server, arn, default=True)
    assert result["ok"] is True and result["VersionId"] == "v2"
    assert (await call(server, "iam_policy_get", policy_arn=arn))["document"] == WRITE_POLICY


async def test_policy_version_create_limit_explains(server):
    arn = await _policy(server)
    for _ in range(4):
        await _version(server, arn)
    with pytest.raises(ToolError, match="LimitExceeded.*at most five versions"):
        await _version(server, arn)


async def test_policy_version_create_needs_confirm(server):
    arn = await _policy(server)
    with pytest.raises(ToolError, match="CreatePolicyVersion refused: this is destructive"):
        await call(
            server, "iam_policy_version_create", policy_arn=arn, policy_document=WRITE_POLICY
        )


async def test_policy_version_create_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="CreatePolicyVersion refused: the server is read-only"):
        await call(
            read_only_server,
            "iam_policy_version_create",
            policy_arn=POLICY_ARN,
            policy_document=WRITE_POLICY,
            confirm=True,
        )


# iam_policy_version_delete


async def test_policy_version_delete(server):
    arn = await _policy(server)
    await _version(server, arn)
    result = await call(
        server, "iam_policy_version_delete", policy_arn=arn, version_id="v2", confirm=True
    )
    assert result == {"ok": True, "policy_arn": arn, "version_id": "v2"}
    versions = await call(server, "iam_policy_version_list", policy_arn=arn)
    assert [v["VersionId"] for v in versions["items"]] == ["v1"]


async def test_policy_version_delete_default_fails(server):
    arn = await _policy(server)
    with pytest.raises(ToolError, match="DeletePolicyVersion failed: DeleteConflict"):
        await call(
            server, "iam_policy_version_delete", policy_arn=arn, version_id="v1", confirm=True
        )


async def test_policy_version_delete_needs_confirm(server):
    with pytest.raises(ToolError, match="DeletePolicyVersion refused: this is destructive"):
        await call(server, "iam_policy_version_delete", policy_arn=POLICY_ARN, version_id="v2")


async def test_policy_version_delete_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="DeletePolicyVersion refused: the server is read-only"):
        await call(
            read_only_server,
            "iam_policy_version_delete",
            policy_arn=POLICY_ARN,
            version_id="v2",
            confirm=True,
        )


# iam_policy_version_set_default


async def test_policy_version_set_default(server):
    arn = await _policy(server)
    await _version(server, arn)
    result = await call(
        server, "iam_policy_version_set_default", policy_arn=arn, version_id="v2", confirm=True
    )
    assert result == {"ok": True, "policy_arn": arn, "default_version_id": "v2"}
    assert (await call(server, "iam_policy_get", policy_arn=arn))["policy"]["DefaultVersionId"] == (
        "v2"
    )


async def test_policy_version_set_default_missing(server):
    arn = await _policy(server)
    with pytest.raises(ToolError, match="SetDefaultPolicyVersion failed"):
        await call(
            server, "iam_policy_version_set_default", policy_arn=arn, version_id="v9", confirm=True
        )


async def test_policy_version_set_default_needs_confirm(server):
    with pytest.raises(ToolError, match="SetDefaultPolicyVersion refused: this is destructive"):
        await call(server, "iam_policy_version_set_default", policy_arn=POLICY_ARN, version_id="v2")


async def test_policy_version_set_default_refused_read_only(read_only_server):
    with pytest.raises(ToolError, match="SetDefaultPolicyVersion refused: the server is read-only"):
        await call(
            read_only_server,
            "iam_policy_version_set_default",
            policy_arn=POLICY_ARN,
            version_id="v2",
            confirm=True,
        )


# iam_policy_entities_list


async def test_policy_entities_list(server):
    arn = await _policy(server)
    await call(server, "iam_user_create", user_name="alice")
    await call(
        server,
        "iam_policy_attach",
        principal_type="user",
        principal_name="alice",
        policy_arn=arn,
        confirm=True,
    )
    result = await call(server, "iam_policy_entities_list", policy_arn=arn)
    [entity] = result["items"]
    assert entity["type"] == "user" and entity["name"] == "alice" and entity["id"]
    assert result["truncated"] is False and result["next_token"] is None


async def test_policy_entities_list_filter_and_pagination(stub):
    stub.iam.add_response(
        "list_entities_for_policy",
        {
            "PolicyRoles": [{"RoleName": "reader", "RoleId": "AROAREADER0000000001"}],
            "IsTruncated": True,
            "Marker": "m",
        },
        {"PolicyArn": POLICY_ARN, "EntityFilter": "Role", "MaxItems": 100},
    )
    result = await call(
        stub.server, "iam_policy_entities_list", policy_arn=POLICY_ARN, entity_filter="Role"
    )
    assert result == {
        "items": [{"type": "role", "name": "reader", "id": "AROAREADER0000000001"}],
        "next_token": "m",
        "truncated": True,
    }


async def test_policy_entities_list_missing_policy(stub):
    stub.iam.add_client_error(
        "list_entities_for_policy", **client_error("NoSuchEntity", "Policy not found", 404)
    )
    with pytest.raises(ToolError, match="ListEntitiesForPolicy failed: NoSuchEntity"):
        await call(stub.server, "iam_policy_entities_list", policy_arn=POLICY_ARN)

"""Live tests against a real Wasabi account (PRD sections 5.6 and 8.4).

They run only with ``uv run pytest -m live``, ``WASABI_LIVE=1`` and Wasabi keys in the
environment. Every bucket, user, group and policy is named ``wasabi-mcp-test-<id>`` by
``new_name``, which records it; a session finalizer deletes whatever is left, even when
a test fails part-way.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from typing import Any, Literal

import pytest
from botocore.exceptions import ClientError
from mcp.server.mcpserver import MCPServer

from tests.helpers import call
from wasabi_mcp.clients import Clients
from wasabi_mcp.config import Settings
from wasabi_mcp.server import build_server

PREFIX = "wasabi-mcp-test-"
Kind = Literal["bucket", "user", "group", "policy"]
GONE = {"NoSuchBucket", "NoSuchEntity", "NoSuchKey", "NoSuchUpload", "NoSuchLifecycleConfiguration"}


@dataclass
class Created:
    """Names this run handed out, by kind, for the finalizer to remove."""

    names: dict[str, list[str]] = field(
        default_factory=lambda: {"bucket": [], "user": [], "group": [], "policy": []}
    )


@pytest.fixture(scope="session")
def live_settings() -> Settings:
    """Real Wasabi settings from the environment with writes allowed.

    Skips unless ``WASABI_LIVE=1`` and both Wasabi keys are set.
    """
    if os.environ.get("WASABI_LIVE") != "1":
        pytest.skip("set WASABI_LIVE=1 (and Wasabi keys) to run live tests")
    settings = Settings.from_env()
    if not (settings.access_key_id and settings.secret_access_key):
        pytest.skip("set WASABI_ACCESS_KEY_ID and WASABI_SECRET_ACCESS_KEY to run live tests")
    return replace(settings, read_only=False)


@pytest.fixture
def live_server(live_settings: Settings) -> MCPServer:
    return build_server(live_settings)


@pytest.fixture(scope="session")
def _created(live_settings: Settings) -> Iterator[Created]:
    created = Created()
    yield created
    failures = cleanup(live_settings, created)
    assert not failures, f"live cleanup left resources behind: {failures}"


@pytest.fixture
def new_name(_created: Created) -> Callable[[Kind], str]:
    """``new_name("bucket")`` -> a unique ``wasabi-mcp-test-`` name, recorded for cleanup."""

    def make(kind: Kind) -> str:
        name = f"{PREFIX}{uuid.uuid4().hex[:16]}"
        _created.names[kind].append(name)
        return name

    return make


@pytest.fixture
async def bucket(live_server: MCPServer, new_name: Callable[[Kind], str]) -> str:
    """A new, empty bucket in the default region, removed by the finalizer."""
    name = new_name("bucket")
    await call(live_server, "bucket_create", bucket=name)
    return name


# --- cleanup (boto3 directly, so it doesn't depend on the tools under test) -------------


def _ignore_gone(action: Callable[[], Any]) -> None:
    try:
        action()
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in GONE:
            raise


def _empty_and_delete_bucket(clients: Clients, bucket: str) -> None:
    s3 = clients.s3()
    try:
        region = s3.get_bucket_location(Bucket=bucket).get("LocationConstraint") or "us-east-1"
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in GONE:
            return
        raise
    s3 = clients.s3(region)
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket):
        entries = page.get("Versions", []) + page.get("DeleteMarkers", [])
        objects = [{"Key": e["Key"], "VersionId": e["VersionId"]} for e in entries]
        if objects:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
    for page in s3.get_paginator("list_multipart_uploads").paginate(Bucket=bucket):
        for upload in page.get("Uploads", []):
            s3.abort_multipart_upload(Bucket=bucket, Key=upload["Key"], UploadId=upload["UploadId"])
    _ignore_gone(lambda: s3.delete_bucket(Bucket=bucket))


def _delete_user(iam: Any, user: str) -> None:
    for group in iam.list_groups_for_user(UserName=user).get("Groups", []):
        iam.remove_user_from_group(GroupName=group["GroupName"], UserName=user)
    for policy in iam.list_attached_user_policies(UserName=user).get("AttachedPolicies", []):
        iam.detach_user_policy(UserName=user, PolicyArn=policy["PolicyArn"])
    for name in iam.list_user_policies(UserName=user).get("PolicyNames", []):
        iam.delete_user_policy(UserName=user, PolicyName=name)
    for key in iam.list_access_keys(UserName=user).get("AccessKeyMetadata", []):
        iam.delete_access_key(UserName=user, AccessKeyId=key["AccessKeyId"])
    _ignore_gone(lambda: iam.delete_login_profile(UserName=user))
    iam.delete_user(UserName=user)


def _delete_group(iam: Any, group: str) -> None:
    for user in iam.get_group(GroupName=group).get("Users", []):
        iam.remove_user_from_group(GroupName=group, UserName=user["UserName"])
    for policy in iam.list_attached_group_policies(GroupName=group).get("AttachedPolicies", []):
        iam.detach_group_policy(GroupName=group, PolicyArn=policy["PolicyArn"])
    for name in iam.list_group_policies(GroupName=group).get("PolicyNames", []):
        iam.delete_group_policy(GroupName=group, PolicyName=name)
    iam.delete_group(GroupName=group)


def _delete_policy(iam: Any, arn: str) -> None:
    entities = iam.list_entities_for_policy(PolicyArn=arn)
    for user in entities.get("PolicyUsers", []):
        iam.detach_user_policy(UserName=user["UserName"], PolicyArn=arn)
    for group in entities.get("PolicyGroups", []):
        iam.detach_group_policy(GroupName=group["GroupName"], PolicyArn=arn)
    for role in entities.get("PolicyRoles", []):
        iam.detach_role_policy(RoleName=role["RoleName"], PolicyArn=arn)
    for version in iam.list_policy_versions(PolicyArn=arn).get("Versions", []):
        if not version["IsDefaultVersion"]:
            iam.delete_policy_version(PolicyArn=arn, VersionId=version["VersionId"])
    iam.delete_policy(PolicyArn=arn)


def cleanup(settings: Settings, created: Created) -> dict[str, str]:
    """Delete every recorded resource that still exists; return what couldn't be removed."""
    clients = Clients(settings)
    iam = clients.iam()
    failures: dict[str, str] = {}

    def attempt(label: str, action: Callable[[], None]) -> None:
        try:
            _ignore_gone(action)
        except Exception as exc:  # report every leftover, not just the first
            failures[label] = str(exc)

    for bucket in created.names["bucket"]:
        attempt(f"bucket {bucket}", lambda b=bucket: _empty_and_delete_bucket(clients, b))
    for user in created.names["user"]:
        attempt(f"user {user}", lambda u=user: _delete_user(iam, u))
    for group in created.names["group"]:
        attempt(f"group {group}", lambda g=group: _delete_group(iam, g))
    wanted = set(created.names["policy"])
    if wanted:
        arns = [
            policy["Arn"]
            for page in iam.get_paginator("list_policies").paginate(Scope="Local")
            for policy in page.get("Policies", [])
            if policy["PolicyName"] in wanted
        ]
        for arn in arns:
            attempt(f"policy {arn}", lambda a=arn: _delete_policy(iam, a))
    return failures

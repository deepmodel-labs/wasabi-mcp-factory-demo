"""IAM managed policies and their versions (PRD section 6.12)."""

from __future__ import annotations

from typing import Any, Literal

from botocore.exceptions import BotoCoreError, ClientError
from mcp.server.mcpserver import MCPServer

from wasabi_mcp.errors import explain
from wasabi_mcp.safety import require_confirmed
from wasabi_mcp.tools import DESTRUCTIVE, READ, ToolContext, clean
from wasabi_mcp.tools._iam import (
    DEFAULT_MAX_ITEMS,
    DELETE_CONFLICT,
    LIMIT_EXCEEDED,
    POLICY_VERSION_LIMIT_HINT,
    collect,
    decode_policy,
    delete_conflict_hint,
    invoke,
    page_params,
    page_result,
    policy_json,
    principal_action,
    principal_key,
    principal_method,
)

PolicyScope = Literal["All", "AWS", "Local"]
EntityFilter = Literal["User", "Group", "Role"]

POLICY_DELETE_HINTS = {
    DELETE_CONFLICT: delete_conflict_hint("attached users, groups or roles, or old versions")
}
VERSION_CREATE_HINTS = {LIMIT_EXCEEDED: POLICY_VERSION_LIMIT_HINT}

_ENTITY_KEYS = (
    ("PolicyUsers", "user", "UserName", "UserId"),
    ("PolicyGroups", "group", "GroupName", "GroupId"),
    ("PolicyRoles", "role", "RoleName", "RoleId"),
)


def version_json(version: dict[str, Any]) -> dict[str, Any]:
    """A policy version with its document (when present) as a JSON object."""
    cleaned = clean(version)
    if "Document" in cleaned:
        cleaned["Document"] = decode_policy(cleaned["Document"])
    return cleaned


def entities(response: dict[str, Any]) -> list[dict[str, Any]]:
    """The users, groups and roles of a ListEntitiesForPolicy response, as one list."""
    return [
        {"type": kind, "name": entity.get(name_key), "id": entity.get(id_key)}
        for list_key, kind, name_key, id_key in _ENTITY_KEYS
        for entity in response.get(list_key, [])
    ]


def _attached_entities(iam: Any, policy_arn: str) -> list[dict[str, Any]]:
    """Every user, group and role ``policy_arn`` is attached to, across all pages."""
    try:
        pages = iam.get_paginator("list_entities_for_policy").paginate(PolicyArn=policy_arn)
        return [entity for page in pages for entity in entities(page)]
    except (ClientError, BotoCoreError) as exc:
        raise explain(exc, "ListEntitiesForPolicy") from exc


def _cascade_policy(iam: Any, policy_arn: str) -> list[str]:
    """Detach ``policy_arn`` everywhere and delete its non-default versions."""
    attached = _attached_entities(iam, policy_arn)
    versions = collect(
        iam, "list_policy_versions", "Versions", "ListPolicyVersions", PolicyArn=policy_arn
    )
    steps: list[str] = []
    for entity in attached:
        kind = entity["type"]
        action = principal_action(kind, "Detach{}Policy")
        invoke(
            action,
            principal_method(iam, kind, "detach_{}_policy"),
            PolicyArn=policy_arn,
            **{principal_key(kind): entity["name"]},
        )
        steps.append(f"{action} {entity['name']}")
    for version in versions:
        if not version.get("IsDefaultVersion"):
            invoke(
                "DeletePolicyVersion",
                iam.delete_policy_version,
                PolicyArn=policy_arn,
                VersionId=version["VersionId"],
            )
            steps.append(f"DeletePolicyVersion {version['VersionId']}")
    return steps


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings
    clients = ctx.clients

    @server.tool(name="iam_policy_list", annotations=READ)
    def iam_policy_list(
        scope: PolicyScope = "All",
        only_attached: bool = False,
        path_prefix: str | None = None,
        max_items: int = DEFAULT_MAX_ITEMS,
        continuation_token: str | None = None,
    ) -> dict[str, Any]:
        """List managed policies: scope All, AWS (Wasabi's system policies) or Local
        (customer-managed); only_attached=true limits to policies in use. Returns names,
        ARNs, default versions and attachment counts, plus pagination."""
        response = invoke(
            "ListPolicies",
            clients.iam().list_policies,
            Scope=scope,
            OnlyAttached=only_attached,
            PathPrefix=path_prefix,
            **page_params(max_items, continuation_token),
        )
        return page_result(response, "Policies")

    @server.tool(name="iam_policy_get", annotations=READ)
    def iam_policy_get(policy_arn: str) -> dict[str, Any]:
        """Show a managed policy and its default version's document as JSON."""
        iam = clients.iam()
        policy = invoke("GetPolicy", iam.get_policy, PolicyArn=policy_arn)["Policy"]
        version = invoke(
            "GetPolicyVersion",
            iam.get_policy_version,
            PolicyArn=policy_arn,
            VersionId=policy["DefaultVersionId"],
        )["PolicyVersion"]
        return {"policy": clean(policy), "document": decode_policy(version.get("Document"))}

    @server.tool(name="iam_policy_create", annotations=DESTRUCTIVE)
    def iam_policy_create(
        policy_name: str,
        policy_document: dict[str, Any] | str,
        description: str | None = None,
        path: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Create a customer-managed policy from a document (dict or JSON); needs
        confirm=true. Returns the new policy with its ARN."""
        require_confirmed(settings, "CreatePolicy", confirm)
        document = policy_json(policy_document)
        response = invoke(
            "CreatePolicy",
            clients.iam().create_policy,
            PolicyName=policy_name,
            PolicyDocument=document,
            Description=description,
            Path=path,
        )
        return {"ok": True, "policy": clean(response["Policy"])}

    @server.tool(name="iam_policy_delete", annotations=DESTRUCTIVE)
    def iam_policy_delete(
        policy_arn: str, cascade: bool = False, confirm: bool = False
    ) -> dict[str, Any]:
        """Delete a customer-managed policy; needs confirm=true. With cascade=true it first
        detaches it from every user, group and role and deletes its non-default versions,
        listing each step."""
        require_confirmed(settings, "DeletePolicy", confirm)
        iam = clients.iam()
        steps = _cascade_policy(iam, policy_arn) if cascade else []
        invoke("DeletePolicy", iam.delete_policy, hints=POLICY_DELETE_HINTS, PolicyArn=policy_arn)
        return {"ok": True, "policy_arn": policy_arn, "steps": [*steps, "DeletePolicy"]}

    @server.tool(name="iam_policy_version_list", annotations=READ)
    def iam_policy_version_list(
        policy_arn: str,
        max_items: int = DEFAULT_MAX_ITEMS,
        continuation_token: str | None = None,
    ) -> dict[str, Any]:
        """List a managed policy's versions (at most five) with which one is the default,
        plus pagination."""
        response = invoke(
            "ListPolicyVersions",
            clients.iam().list_policy_versions,
            PolicyArn=policy_arn,
            **page_params(max_items, continuation_token),
        )
        return page_result(response, "Versions", version_json)

    @server.tool(name="iam_policy_version_get", annotations=READ)
    def iam_policy_version_get(policy_arn: str, version_id: str) -> dict[str, Any]:
        """Show one version of a managed policy, with its document as JSON."""
        response = invoke(
            "GetPolicyVersion",
            clients.iam().get_policy_version,
            PolicyArn=policy_arn,
            VersionId=version_id,
        )
        return version_json(response["PolicyVersion"])

    @server.tool(name="iam_policy_version_create", annotations=DESTRUCTIVE)
    def iam_policy_version_create(
        policy_arn: str,
        policy_document: dict[str, Any] | str,
        set_as_default: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Add a new version of a managed policy (dict or JSON), optionally making it the
        default; needs confirm=true. A policy holds at most five versions."""
        require_confirmed(settings, "CreatePolicyVersion", confirm)
        document = policy_json(policy_document)
        response = invoke(
            "CreatePolicyVersion",
            clients.iam().create_policy_version,
            hints=VERSION_CREATE_HINTS,
            PolicyArn=policy_arn,
            PolicyDocument=document,
            SetAsDefault=set_as_default,
        )
        return {"ok": True, "policy_arn": policy_arn, **version_json(response["PolicyVersion"])}

    @server.tool(name="iam_policy_version_delete", annotations=DESTRUCTIVE)
    def iam_policy_version_delete(
        policy_arn: str, version_id: str, confirm: bool = False
    ) -> dict[str, Any]:
        """Delete a non-default version of a managed policy; needs confirm=true."""
        require_confirmed(settings, "DeletePolicyVersion", confirm)
        invoke(
            "DeletePolicyVersion",
            clients.iam().delete_policy_version,
            PolicyArn=policy_arn,
            VersionId=version_id,
        )
        return {"ok": True, "policy_arn": policy_arn, "version_id": version_id}

    @server.tool(name="iam_policy_version_set_default", annotations=DESTRUCTIVE)
    def iam_policy_version_set_default(
        policy_arn: str, version_id: str, confirm: bool = False
    ) -> dict[str, Any]:
        """Make a version the default (active) one for a managed policy, changing access for
        everyone it's attached to; needs confirm=true."""
        require_confirmed(settings, "SetDefaultPolicyVersion", confirm)
        invoke(
            "SetDefaultPolicyVersion",
            clients.iam().set_default_policy_version,
            PolicyArn=policy_arn,
            VersionId=version_id,
        )
        return {"ok": True, "policy_arn": policy_arn, "default_version_id": version_id}

    @server.tool(name="iam_policy_entities_list", annotations=READ)
    def iam_policy_entities_list(
        policy_arn: str,
        entity_filter: EntityFilter | None = None,
        max_items: int = DEFAULT_MAX_ITEMS,
        continuation_token: str | None = None,
    ) -> dict[str, Any]:
        """List the users, groups and roles a managed policy is attached to, optionally
        only one kind. Each item has type, name and ID; plus pagination."""
        response = invoke(
            "ListEntitiesForPolicy",
            clients.iam().list_entities_for_policy,
            PolicyArn=policy_arn,
            EntityFilter=entity_filter,
            **page_params(max_items, continuation_token),
        )
        truncated = bool(response.get("IsTruncated"))
        return {
            "items": entities(response),
            "next_token": response.get("Marker") if truncated else None,
            "truncated": truncated,
        }

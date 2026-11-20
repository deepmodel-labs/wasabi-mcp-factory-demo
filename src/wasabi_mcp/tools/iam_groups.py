"""IAM groups and group membership (PRD section 6.12)."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from wasabi_mcp.safety import require_confirmed, require_writable
from wasabi_mcp.tools import DESTRUCTIVE, READ, WRITE, ToolContext, clean
from wasabi_mcp.tools._iam import (
    DEFAULT_MAX_ITEMS,
    DELETE_CONFLICT,
    collect,
    delete_conflict_hint,
    invoke,
    page_params,
    page_result,
)

GROUP_DELETE_HINTS = {DELETE_CONFLICT: delete_conflict_hint("members or policies")}


def _cascade_group(iam: Any, group_name: str) -> list[str]:
    """Remove members and policies from ``group_name``; return the steps taken."""
    members = collect(iam, "get_group", "Users", "GetGroup", GroupName=group_name)
    attached = collect(
        iam,
        "list_attached_group_policies",
        "AttachedPolicies",
        "ListAttachedGroupPolicies",
        GroupName=group_name,
    )
    inline = collect(
        iam, "list_group_policies", "PolicyNames", "ListGroupPolicies", GroupName=group_name
    )
    steps: list[str] = []
    for user in members:
        invoke(
            "RemoveUserFromGroup",
            iam.remove_user_from_group,
            GroupName=group_name,
            UserName=user["UserName"],
        )
        steps.append(f"RemoveUserFromGroup {user['UserName']}")
    for policy in attached:
        invoke(
            "DetachGroupPolicy",
            iam.detach_group_policy,
            GroupName=group_name,
            PolicyArn=policy["PolicyArn"],
        )
        steps.append(f"DetachGroupPolicy {policy['PolicyArn']}")
    for name in inline:
        invoke("DeleteGroupPolicy", iam.delete_group_policy, GroupName=group_name, PolicyName=name)
        steps.append(f"DeleteGroupPolicy {name}")
    return steps


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings
    clients = ctx.clients

    @server.tool(name="iam_group_list", annotations=READ)
    def iam_group_list(
        path_prefix: str | None = None,
        max_items: int = DEFAULT_MAX_ITEMS,
        continuation_token: str | None = None,
    ) -> dict[str, Any]:
        """List IAM groups, optionally under a path prefix. Returns groups with name, ID,
        ARN, path and created date, plus pagination."""
        response = invoke(
            "ListGroups",
            clients.iam().list_groups,
            PathPrefix=path_prefix,
            **page_params(max_items, continuation_token),
        )
        return page_result(response, "Groups")

    @server.tool(name="iam_group_get", annotations=READ)
    def iam_group_get(group_name: str) -> dict[str, Any]:
        """Summarise one IAM group: the group, its member user names, and its attached and
        inline policies."""
        iam = clients.iam()
        group = invoke("GetGroup", iam.get_group, GroupName=group_name)["Group"]
        members = collect(iam, "get_group", "Users", "GetGroup", GroupName=group_name)
        attached = collect(
            iam,
            "list_attached_group_policies",
            "AttachedPolicies",
            "ListAttachedGroupPolicies",
            GroupName=group_name,
        )
        inline = collect(
            iam, "list_group_policies", "PolicyNames", "ListGroupPolicies", GroupName=group_name
        )
        return {
            "group": clean(group),
            "members": [user["UserName"] for user in members],
            "attached_policies": clean(attached),
            "inline_policies": list(inline),
        }

    @server.tool(name="iam_group_create", annotations=WRITE)
    def iam_group_create(group_name: str, path: str | None = None) -> dict[str, Any]:
        """Create an IAM group. Returns the new group with its ARN."""
        require_writable(settings, "CreateGroup")
        response = invoke(
            "CreateGroup", clients.iam().create_group, GroupName=group_name, Path=path
        )
        return {"ok": True, "group": clean(response["Group"])}

    @server.tool(name="iam_group_update", annotations=WRITE)
    def iam_group_update(
        group_name: str, new_group_name: str | None = None, new_path: str | None = None
    ) -> dict[str, Any]:
        """Rename an IAM group and/or change its path. Returns the group's new name."""
        require_writable(settings, "UpdateGroup")
        invoke(
            "UpdateGroup",
            clients.iam().update_group,
            GroupName=group_name,
            NewGroupName=new_group_name,
            NewPath=new_path,
        )
        return {"ok": True, "group_name": new_group_name or group_name}

    @server.tool(name="iam_group_delete", annotations=DESTRUCTIVE)
    def iam_group_delete(
        group_name: str, cascade: bool = False, confirm: bool = False
    ) -> dict[str, Any]:
        """Delete an IAM group; needs confirm=true. With cascade=true it first removes the
        members and detaches or deletes its policies, and lists each step."""
        require_confirmed(settings, "DeleteGroup", confirm)
        iam = clients.iam()
        steps = _cascade_group(iam, group_name) if cascade else []
        invoke("DeleteGroup", iam.delete_group, hints=GROUP_DELETE_HINTS, GroupName=group_name)
        return {"ok": True, "group_name": group_name, "steps": [*steps, "DeleteGroup"]}

    @server.tool(name="iam_group_add_user", annotations=DESTRUCTIVE)
    def iam_group_add_user(
        group_name: str, user_name: str, confirm: bool = False
    ) -> dict[str, Any]:
        """Add an IAM user to a group, granting them the group's policies; needs
        confirm=true."""
        require_confirmed(settings, "AddUserToGroup", confirm)
        invoke(
            "AddUserToGroup",
            clients.iam().add_user_to_group,
            GroupName=group_name,
            UserName=user_name,
        )
        return {"ok": True, "group_name": group_name, "user_name": user_name}

    @server.tool(name="iam_group_remove_user", annotations=DESTRUCTIVE)
    def iam_group_remove_user(
        group_name: str, user_name: str, confirm: bool = False
    ) -> dict[str, Any]:
        """Remove an IAM user from a group, taking away the group's policies; needs
        confirm=true."""
        require_confirmed(settings, "RemoveUserFromGroup", confirm)
        invoke(
            "RemoveUserFromGroup",
            clients.iam().remove_user_from_group,
            GroupName=group_name,
            UserName=user_name,
        )
        return {"ok": True, "group_name": group_name, "user_name": user_name}

    @server.tool(name="iam_user_group_list", annotations=READ)
    def iam_user_group_list(
        user_name: str,
        max_items: int = DEFAULT_MAX_ITEMS,
        continuation_token: str | None = None,
    ) -> dict[str, Any]:
        """List the groups an IAM user belongs to, plus pagination."""
        response = invoke(
            "ListGroupsForUser",
            clients.iam().list_groups_for_user,
            UserName=user_name,
            **page_params(max_items, continuation_token),
        )
        return page_result(response, "Groups")

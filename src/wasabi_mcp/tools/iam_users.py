"""IAM users and login profiles (PRD section 6.11)."""

from __future__ import annotations

import secrets
import string
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from mcp.server.mcpserver import MCPServer

from wasabi_mcp.errors import explain
from wasabi_mcp.safety import require_confirmed, require_writable
from wasabi_mcp.tools import DESTRUCTIVE, READ, WRITE, ToolContext, clean
from wasabi_mcp.tools._iam import (
    DEFAULT_MAX_ITEMS,
    DELETE_CONFLICT,
    NO_SUCH_ENTITY,
    SECRET_NOTE,
    collect,
    delete_conflict_hint,
    error_code,
    invoke,
    page_params,
    page_result,
)

PASSWORD_LENGTH = 24
_PASSWORD_SYMBOLS = "!@#$%^&*()-_=+[]{}"
_PASSWORD_CLASSES = (
    string.ascii_uppercase,
    string.ascii_lowercase,
    string.digits,
    _PASSWORD_SYMBOLS,
)

USER_DELETE_HINTS = {
    DELETE_CONFLICT: delete_conflict_hint(
        "access keys, a login profile, group memberships, attached or inline policies, "
        "or MFA devices"
    )
}


def generate_password(length: int = PASSWORD_LENGTH) -> str:
    """A random password with at least one upper, lower, digit and symbol character."""
    alphabet = "".join(_PASSWORD_CLASSES)
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if all(any(ch in chars for ch in password) for chars in _PASSWORD_CLASSES):
            return password


def _key_summary(key: dict[str, Any]) -> dict[str, Any]:
    return clean({k: key.get(k) for k in ("AccessKeyId", "Status", "CreateDate")})


def _cascade_user(iam: Any, user_name: str) -> list[str]:
    """Remove everything that blocks deleting ``user_name``; return the steps taken."""
    steps: list[str] = []
    for key in collect(
        iam, "list_access_keys", "AccessKeyMetadata", "ListAccessKeys", UserName=user_name
    ):
        invoke(
            "DeleteAccessKey",
            iam.delete_access_key,
            UserName=user_name,
            AccessKeyId=key["AccessKeyId"],
        )
        steps.append(f"DeleteAccessKey {key['AccessKeyId']}")
    if _login_profile(iam, user_name) is not None:
        invoke("DeleteLoginProfile", iam.delete_login_profile, UserName=user_name)
        steps.append("DeleteLoginProfile")
    for group in collect(
        iam, "list_groups_for_user", "Groups", "ListGroupsForUser", UserName=user_name
    ):
        invoke(
            "RemoveUserFromGroup",
            iam.remove_user_from_group,
            UserName=user_name,
            GroupName=group["GroupName"],
        )
        steps.append(f"RemoveUserFromGroup {group['GroupName']}")
    for policy in collect(
        iam,
        "list_attached_user_policies",
        "AttachedPolicies",
        "ListAttachedUserPolicies",
        UserName=user_name,
    ):
        invoke(
            "DetachUserPolicy",
            iam.detach_user_policy,
            UserName=user_name,
            PolicyArn=policy["PolicyArn"],
        )
        steps.append(f"DetachUserPolicy {policy['PolicyArn']}")
    for name in collect(
        iam, "list_user_policies", "PolicyNames", "ListUserPolicies", UserName=user_name
    ):
        invoke("DeleteUserPolicy", iam.delete_user_policy, UserName=user_name, PolicyName=name)
        steps.append(f"DeleteUserPolicy {name}")
    for device in collect(
        iam, "list_mfa_devices", "MFADevices", "ListMFADevices", UserName=user_name
    ):
        serial = device["SerialNumber"]
        invoke(
            "DeactivateMFADevice",
            iam.deactivate_mfa_device,
            UserName=user_name,
            SerialNumber=serial,
        )
        steps.append(f"DeactivateMFADevice {serial}")
        if ":mfa/" in serial:
            invoke("DeleteVirtualMFADevice", iam.delete_virtual_mfa_device, SerialNumber=serial)
            steps.append(f"DeleteVirtualMFADevice {serial}")
    return steps


def _login_profile(iam: Any, user_name: str) -> dict[str, Any] | None:
    """The user's login profile, or ``None`` when they have none."""
    try:
        return iam.get_login_profile(UserName=user_name)["LoginProfile"]
    except (ClientError, BotoCoreError) as exc:
        if _is_missing_profile(exc):
            return None
        raise explain(exc, "GetLoginProfile") from exc


def _is_missing_profile(exc: Exception) -> bool:
    """A ``NoSuchEntity`` about the login profile, not about the user."""
    return error_code(exc) == NO_SUCH_ENTITY and "login profile" in str(exc).lower()


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings
    clients = ctx.clients

    @server.tool(name="iam_user_list", annotations=READ)
    def iam_user_list(
        path_prefix: str | None = None,
        max_items: int = DEFAULT_MAX_ITEMS,
        continuation_token: str | None = None,
    ) -> dict[str, Any]:
        """List IAM users, optionally under a path prefix such as /contractors/. Returns
        users with name, ID, ARN, path and created date, plus pagination."""
        response = invoke(
            "ListUsers",
            clients.iam().list_users,
            PathPrefix=path_prefix,
            **page_params(max_items, continuation_token),
        )
        return page_result(response, "Users")

    @server.tool(name="iam_user_get", annotations=READ)
    def iam_user_get(user_name: str) -> dict[str, Any]:
        """Summarise one IAM user: the user, their groups, attached and inline policies,
        and access keys (IDs, status and created date only, never secrets)."""
        iam = clients.iam()
        user = invoke("GetUser", iam.get_user, UserName=user_name)["User"]
        groups = collect(
            iam, "list_groups_for_user", "Groups", "ListGroupsForUser", UserName=user_name
        )
        attached = collect(
            iam,
            "list_attached_user_policies",
            "AttachedPolicies",
            "ListAttachedUserPolicies",
            UserName=user_name,
        )
        inline = collect(
            iam, "list_user_policies", "PolicyNames", "ListUserPolicies", UserName=user_name
        )
        keys = collect(
            iam, "list_access_keys", "AccessKeyMetadata", "ListAccessKeys", UserName=user_name
        )
        return {
            "user": clean(user),
            "groups": [group["GroupName"] for group in groups],
            "attached_policies": clean(attached),
            "inline_policies": list(inline),
            "access_keys": [_key_summary(key) for key in keys],
        }

    @server.tool(name="iam_user_create", annotations=WRITE)
    def iam_user_create(user_name: str, path: str | None = None) -> dict[str, Any]:
        """Create an IAM user (no keys or password yet). Returns the new user with its
        ARN."""
        require_writable(settings, "CreateUser")
        response = invoke("CreateUser", clients.iam().create_user, UserName=user_name, Path=path)
        return {"ok": True, "user": clean(response["User"])}

    @server.tool(name="iam_user_update", annotations=WRITE)
    def iam_user_update(
        user_name: str, new_user_name: str | None = None, new_path: str | None = None
    ) -> dict[str, Any]:
        """Rename an IAM user and/or change their path. Returns the user's new name."""
        require_writable(settings, "UpdateUser")
        invoke(
            "UpdateUser",
            clients.iam().update_user,
            UserName=user_name,
            NewUserName=new_user_name,
            NewPath=new_path,
        )
        return {"ok": True, "user_name": new_user_name or user_name}

    @server.tool(name="iam_user_delete", annotations=DESTRUCTIVE)
    def iam_user_delete(
        user_name: str, cascade: bool = False, confirm: bool = False
    ) -> dict[str, Any]:
        """Delete an IAM user; needs confirm=true. With cascade=true it first removes the
        user's keys, login profile, group memberships, policies and MFA devices, and lists
        each step."""
        require_confirmed(settings, "DeleteUser", confirm)
        iam = clients.iam()
        steps = _cascade_user(iam, user_name) if cascade else []
        invoke("DeleteUser", iam.delete_user, hints=USER_DELETE_HINTS, UserName=user_name)
        return {"ok": True, "user_name": user_name, "steps": [*steps, "DeleteUser"]}

    @server.tool(name="iam_login_profile_get", annotations=READ)
    def iam_login_profile_get(user_name: str) -> dict[str, Any]:
        """Show whether an IAM user has a console password (login profile), with its
        created date and reset flag. Returns {"configured": false} when there is none."""
        profile = _login_profile(clients.iam(), user_name)
        if profile is None:
            return {"configured": False}
        return {"configured": True, **clean(profile)}

    @server.tool(name="iam_login_profile_create", annotations=DESTRUCTIVE)
    def iam_login_profile_create(
        user_name: str,
        password: str | None = None,
        password_reset_required: bool = True,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Give an IAM user a console password; needs confirm=true. A strong password is
        generated when none is given, and returned once as `password`."""
        require_confirmed(settings, "CreateLoginProfile", confirm)
        chosen = password or generate_password()
        response = invoke(
            "CreateLoginProfile",
            clients.iam().create_login_profile,
            UserName=user_name,
            Password=chosen,
            PasswordResetRequired=password_reset_required,
        )
        profile = response["LoginProfile"]
        return {
            "ok": True,
            "user_name": profile["UserName"],
            "password": chosen,
            "password_reset_required": password_reset_required,
            "create_date": clean(profile.get("CreateDate")),
            "note": SECRET_NOTE,
        }

    @server.tool(name="iam_login_profile_update", annotations=DESTRUCTIVE)
    def iam_login_profile_update(
        user_name: str,
        password: str | None = None,
        password_reset_required: bool | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Change an IAM user's console password and/or whether they must reset it at next
        sign-in; needs confirm=true. Never returns the password."""
        require_confirmed(settings, "UpdateLoginProfile", confirm)
        invoke(
            "UpdateLoginProfile",
            clients.iam().update_login_profile,
            UserName=user_name,
            Password=password,
            PasswordResetRequired=password_reset_required,
        )
        return {"ok": True, "user_name": user_name}

    @server.tool(name="iam_login_profile_delete", annotations=DESTRUCTIVE)
    def iam_login_profile_delete(user_name: str, confirm: bool = False) -> dict[str, Any]:
        """Remove an IAM user's console password so they can't sign in to the console;
        needs confirm=true. Access keys keep working."""
        require_confirmed(settings, "DeleteLoginProfile", confirm)
        invoke("DeleteLoginProfile", clients.iam().delete_login_profile, UserName=user_name)
        return {"ok": True, "user_name": user_name}

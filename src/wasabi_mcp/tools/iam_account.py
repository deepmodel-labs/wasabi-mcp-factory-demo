"""IAM account settings and MFA (PRD section 6.12)."""

from __future__ import annotations

import base64
from typing import Any, Literal

from botocore.exceptions import BotoCoreError, ClientError
from mcp.server.mcpserver import MCPServer

from wasabi_mcp import rest
from wasabi_mcp.errors import explain
from wasabi_mcp.safety import require_confirmed, require_writable
from wasabi_mcp.tools import DESTRUCTIVE, READ, WRITE, ToolContext, _secrets, clean
from wasabi_mcp.tools._iam import (
    DEFAULT_MAX_ITEMS,
    NO_SUCH_ENTITY,
    SECRET_NOTE,
    error_code,
    invoke,
    page_params,
    page_result,
    query_result,
)

DetailFilter = Literal["User", "Role", "Group", "LocalManagedPolicy", "AWSManagedPolicy"]
AssignmentStatus = Literal["Assigned", "Unassigned", "Any"]

# Fields that hold a virtual MFA device's secret: only the confirmed create returns them.
MFA_SECRET_FIELDS = frozenset({"Base32StringSeed", "QRCodePNG"})

_DETAIL_LISTS = (
    ("UserDetailList", "user"),
    ("GroupDetailList", "group"),
    ("RoleDetailList", "role"),
    ("Policies", "policy"),
)


def without_mfa_secrets(value: Any) -> Any:
    """``value`` with every MFA seed or QR code field removed, at any depth."""
    if isinstance(value, dict):
        return {
            key: without_mfa_secrets(item)
            for key, item in value.items()
            if key not in MFA_SECRET_FIELDS
        }
    if isinstance(value, list):
        return [without_mfa_secrets(item) for item in value]
    return value


def authorization_details(response: dict[str, Any]) -> list[dict[str, Any]]:
    """The users, groups, roles and policies of a GetAccountAuthorizationDetails page, as one
    list with a ``type`` on each item."""
    return [
        {"type": kind, **clean(item)}
        for list_key, kind in _DETAIL_LISTS
        for item in response.get(list_key, [])
    ]


def _text(value: Any) -> str:
    return value.decode("ascii") if isinstance(value, bytes | bytearray) else str(value)


def _base64(value: Any) -> str:
    if isinstance(value, bytes | bytearray):
        return base64.b64encode(value).decode("ascii")
    return str(value)


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings
    clients = ctx.clients

    @server.tool(name="iam_account_summary", annotations=READ)
    def iam_account_summary() -> dict[str, Any]:
        """Summarise IAM usage and quotas for the account: counts of users, groups, roles,
        policies and MFA devices, and their limits."""
        response = invoke("GetAccountSummary", clients.iam().get_account_summary)
        return {"summary": clean(response.get("SummaryMap", {}))}

    @server.tool(name="iam_account_authorization_details", annotations=READ)
    def iam_account_authorization_details(
        filter: list[DetailFilter] | None = None,
        max_items: int = DEFAULT_MAX_ITEMS,
        continuation_token: str | None = None,
    ) -> dict[str, Any]:
        """Snapshot every user, group, role and managed policy with their memberships and
        policy documents (as JSON). filter limits it to User, Role, Group,
        LocalManagedPolicy and/or AWSManagedPolicy. Each item has a type; plus
        pagination."""
        response = invoke(
            "GetAccountAuthorizationDetails",
            clients.iam().get_account_authorization_details,
            Filter=filter,
            **page_params(max_items, continuation_token),
        )
        truncated = bool(response.get("IsTruncated"))
        return {
            "items": authorization_details(response),
            "next_token": response.get("Marker") if truncated else None,
            "truncated": truncated,
        }

    @server.tool(name="iam_password_policy_get", annotations=READ)
    def iam_password_policy_get() -> dict[str, Any]:
        """Show the account's password policy for console users. Returns
        {"configured": false} when none is set."""
        try:
            response = clients.iam().get_account_password_policy()
        except (ClientError, BotoCoreError) as exc:
            if error_code(exc) == NO_SUCH_ENTITY:
                return {"configured": False}
            raise explain(exc, "GetAccountPasswordPolicy") from exc
        return {"configured": True, **clean(response["PasswordPolicy"])}

    @server.tool(name="iam_password_policy_set", annotations=DESTRUCTIVE)
    def iam_password_policy_set(
        minimum_password_length: int | None = None,
        require_symbols: bool | None = None,
        require_numbers: bool | None = None,
        require_uppercase_characters: bool | None = None,
        require_lowercase_characters: bool | None = None,
        allow_users_to_change_password: bool | None = None,
        max_password_age: int | None = None,
        password_reuse_prevention: int | None = None,
        hard_expiry: bool | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Replace the account's password policy; needs confirm=true. Settings left out
        return to their defaults. Returns the resulting policy."""
        require_confirmed(settings, "UpdateAccountPasswordPolicy", confirm)
        iam = clients.iam()
        invoke(
            "UpdateAccountPasswordPolicy",
            iam.update_account_password_policy,
            MinimumPasswordLength=minimum_password_length,
            RequireSymbols=require_symbols,
            RequireNumbers=require_numbers,
            RequireUppercaseCharacters=require_uppercase_characters,
            RequireLowercaseCharacters=require_lowercase_characters,
            AllowUsersToChangePassword=allow_users_to_change_password,
            MaxPasswordAge=max_password_age,
            PasswordReusePrevention=password_reuse_prevention,
            HardExpiry=hard_expiry,
        )
        response = invoke("GetAccountPasswordPolicy", iam.get_account_password_policy)
        return {"ok": True, "password_policy": clean(response["PasswordPolicy"])}

    @server.tool(name="iam_password_policy_delete", annotations=DESTRUCTIVE)
    def iam_password_policy_delete(confirm: bool = False) -> dict[str, Any]:
        """Remove the account's password policy, so console passwords only need the
        defaults; needs confirm=true."""
        require_confirmed(settings, "DeleteAccountPasswordPolicy", confirm)
        invoke("DeleteAccountPasswordPolicy", clients.iam().delete_account_password_policy)
        return {"ok": True}

    @server.tool(name="iam_account_alias_list", annotations=READ)
    def iam_account_alias_list(
        max_items: int = DEFAULT_MAX_ITEMS, continuation_token: str | None = None
    ) -> dict[str, Any]:
        """List the account's alias (used in the console sign-in URL), plus pagination."""
        response = invoke(
            "ListAccountAliases",
            clients.iam().list_account_aliases,
            **page_params(max_items, continuation_token),
        )
        return page_result(response, "AccountAliases")

    @server.tool(name="iam_account_alias_create", annotations=WRITE)
    def iam_account_alias_create(account_alias: str) -> dict[str, Any]:
        """Set the account's alias for the console sign-in URL. An account has at most
        one alias."""
        require_writable(settings, "CreateAccountAlias")
        invoke("CreateAccountAlias", clients.iam().create_account_alias, AccountAlias=account_alias)
        return {"ok": True, "account_alias": account_alias}

    @server.tool(name="iam_account_alias_delete", annotations=DESTRUCTIVE)
    def iam_account_alias_delete(account_alias: str, confirm: bool = False) -> dict[str, Any]:
        """Remove the account's alias, changing the console sign-in URL; needs
        confirm=true."""
        require_confirmed(settings, "DeleteAccountAlias", confirm)
        invoke("DeleteAccountAlias", clients.iam().delete_account_alias, AccountAlias=account_alias)
        return {"ok": True, "account_alias": account_alias}

    @server.tool(name="iam_mfa_device_list", annotations=READ)
    def iam_mfa_device_list(
        user_name: str | None = None,
        assignment_status: AssignmentStatus | None = None,
        max_items: int = DEFAULT_MAX_ITEMS,
        continuation_token: str | None = None,
    ) -> dict[str, Any]:
        """List a user's MFA devices when user_name is given; otherwise list the account's
        virtual MFA devices, filtered by assignment_status (Assigned, Unassigned or Any).
        Returns serial numbers, users and enable dates, never seeds or QR codes."""
        iam = clients.iam()
        paging = page_params(max_items, continuation_token)
        if user_name is not None:
            response = invoke("ListMFADevices", iam.list_mfa_devices, UserName=user_name, **paging)
            return page_result(response, "MFADevices", lambda d: clean(without_mfa_secrets(d)))
        response = invoke(
            "ListVirtualMFADevices",
            iam.list_virtual_mfa_devices,
            AssignmentStatus=assignment_status,
            **paging,
        )
        return page_result(response, "VirtualMFADevices", lambda d: clean(without_mfa_secrets(d)))

    @server.tool(name="iam_user_mfa_get", annotations=READ)
    def iam_user_mfa_get(user_name: str | None = None) -> dict[str, Any]:
        """Show a user's (default: the caller's) MFA status with Wasabi's GetUserMFA: device
        details, plus the authentication date and age of the current credentials."""
        root = rest.iam_query(settings, "GetUserMFA", {"UserName": user_name})
        return {"mfa": without_mfa_secrets(query_result(root, "GetUserMFA"))}

    @server.tool(name="iam_mfa_virtual_device_create", annotations=DESTRUCTIVE)
    def iam_mfa_virtual_device_create(
        device_name: str, path: str | None = None, confirm: bool = False
    ) -> dict[str, Any]:
        """Create a virtual (TOTP) MFA device, the only kind Wasabi supports; needs
        confirm=true. Returns the Base32 seed and a QR code PNG (base64) once, for the user
        to enrol in an authenticator app. With WASABI_MCP_SECRETS_DIR set, both go to one
        new 0600 JSON file instead and only `serial_number` and `secrets_file` are
        returned."""
        require_confirmed(settings, "CreateVirtualMFADevice", confirm)
        directory = _secrets.prepare(settings)
        response = invoke(
            "CreateVirtualMFADevice",
            clients.iam().create_virtual_mfa_device,
            VirtualMFADeviceName=device_name,
            Path=path,
        )
        device = response["VirtualMFADevice"]
        if directory is not None:
            path = _secrets.write(
                directory,
                "iam_mfa_virtual_device_create",
                {
                    "serial_number": device["SerialNumber"],
                    "base32_string_seed": _text(device["Base32StringSeed"]),
                    "qr_code_png": _base64(device["QRCodePNG"]),
                },
                f"Virtual MFA device {device['SerialNumber']}",
            )
            return {
                "ok": True,
                "serial_number": device["SerialNumber"],
                "secrets_file": path,
                "note": _secrets.SAVED_NOTE,
            }
        return {
            "ok": True,
            "serial_number": device["SerialNumber"],
            "base32_seed": _text(device["Base32StringSeed"]),
            "qr_png_base64": _base64(device["QRCodePNG"]),
            "note": SECRET_NOTE,
        }

    @server.tool(name="iam_mfa_device_deactivate", annotations=DESTRUCTIVE)
    def iam_mfa_device_deactivate(
        user_name: str, serial_number: str, delete: bool = False, confirm: bool = False
    ) -> dict[str, Any]:
        """Deactivate a user's MFA device, and with delete=true also delete the virtual
        device; needs confirm=true. The user can then sign in without MFA."""
        require_confirmed(settings, "DeactivateMFADevice", confirm)
        iam = clients.iam()
        invoke(
            "DeactivateMFADevice",
            iam.deactivate_mfa_device,
            UserName=user_name,
            SerialNumber=serial_number,
        )
        steps = ["DeactivateMFADevice"]
        if delete:
            invoke(
                "DeleteVirtualMFADevice",
                iam.delete_virtual_mfa_device,
                SerialNumber=serial_number,
            )
            steps.append("DeleteVirtualMFADevice")
        return {"ok": True, "serial_number": serial_number, "steps": steps}

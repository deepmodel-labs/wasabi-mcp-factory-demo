"""IAM roles and STS (PRD section 6.12)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from wasabi_mcp.config import Settings
from wasabi_mcp.safety import ToolRefused, require_confirmed
from wasabi_mcp.tools import DESTRUCTIVE, READ, ToolContext, _secrets, clean
from wasabi_mcp.tools._iam import (
    ACCESS_DENIED,
    DEFAULT_MAX_ITEMS,
    DELETE_CONFLICT,
    ROOT_ASSUME_ROLE_HINT,
    SECRET_NOTE,
    collect,
    decode_policy,
    delete_conflict_hint,
    invoke,
    page_params,
    page_result,
    policy_json,
)

MIN_SESSION_SECONDS = 900
MIN_ROLE_SESSION_SECONDS = 3600
MAX_SESSION_SECONDS = 43_200

ROLE_DELETE_HINTS = {DELETE_CONFLICT: delete_conflict_hint("attached or inline policies")}
ASSUME_ROLE_HINTS = {ACCESS_DENIED: ROOT_ASSUME_ROLE_HINT}


def check_duration(name: str, seconds: int | None, minimum: int) -> None:
    """Refuse a duration outside ``minimum``..43,200 seconds, before any network call."""
    if seconds is not None and not minimum <= seconds <= MAX_SESSION_SECONDS:
        raise ToolRefused(
            f"{name} must be between {minimum} and {MAX_SESSION_SECONDS} seconds, not {seconds}"
        )


def role_json(role: dict[str, Any]) -> dict[str, Any]:
    """A role with its trust policy as a JSON object."""
    cleaned = clean(role)
    if "AssumeRolePolicyDocument" in cleaned:
        cleaned["AssumeRolePolicyDocument"] = decode_policy(cleaned["AssumeRolePolicyDocument"])
    return cleaned


def temporary_credentials(credentials: dict[str, Any]) -> dict[str, Any]:
    """STS credentials with snake_case names."""
    return {
        "access_key_id": credentials["AccessKeyId"],
        "secret_access_key": credentials["SecretAccessKey"],
        "session_token": credentials["SessionToken"],
        "expiration": clean(credentials.get("Expiration")),
    }


def saved_credentials(
    directory: Path, tool: str, credentials: dict[str, Any], resource: str
) -> dict[str, Any]:
    """Write STS ``credentials`` to a secrets file; return the key ID, expiry and path."""
    temporary = temporary_credentials(credentials)
    path = _secrets.write(directory, tool, temporary, resource)
    return {
        "access_key_id": temporary["access_key_id"],
        "expiration": temporary["expiration"],
        "secrets_file": path,
    }


def caller_identity(response: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Account, user ID and ARN. A user ID equal to the configured key ID is withheld."""
    user_id = response.get("UserId")
    return {
        "account": response.get("Account"),
        "user_id": None if user_id and user_id == settings.access_key_id else user_id,
        "arn": response.get("Arn"),
    }


def _cascade_role(iam: Any, role_name: str) -> list[str]:
    """Detach and delete ``role_name``'s policies; return the steps taken."""
    attached = collect(
        iam,
        "list_attached_role_policies",
        "AttachedPolicies",
        "ListAttachedRolePolicies",
        RoleName=role_name,
    )
    inline = collect(
        iam, "list_role_policies", "PolicyNames", "ListRolePolicies", RoleName=role_name
    )
    steps: list[str] = []
    for policy in attached:
        invoke(
            "DetachRolePolicy",
            iam.detach_role_policy,
            RoleName=role_name,
            PolicyArn=policy["PolicyArn"],
        )
        steps.append(f"DetachRolePolicy {policy['PolicyArn']}")
    for name in inline:
        invoke("DeleteRolePolicy", iam.delete_role_policy, RoleName=role_name, PolicyName=name)
        steps.append(f"DeleteRolePolicy {name}")
    return steps


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings
    clients = ctx.clients

    @server.tool(name="iam_role_list", annotations=READ)
    def iam_role_list(
        path_prefix: str | None = None,
        max_items: int = DEFAULT_MAX_ITEMS,
        continuation_token: str | None = None,
    ) -> dict[str, Any]:
        """List IAM roles, optionally under a path prefix. Returns roles with name, ARN,
        trust policy (as JSON) and created date, plus pagination."""
        response = invoke(
            "ListRoles",
            clients.iam().list_roles,
            PathPrefix=path_prefix,
            **page_params(max_items, continuation_token),
        )
        return page_result(response, "Roles", role_json)

    @server.tool(name="iam_role_get", annotations=READ)
    def iam_role_get(role_name: str) -> dict[str, Any]:
        """Summarise one IAM role: the role with its trust policy as JSON, and its attached
        and inline policies."""
        iam = clients.iam()
        role = invoke("GetRole", iam.get_role, RoleName=role_name)["Role"]
        attached = collect(
            iam,
            "list_attached_role_policies",
            "AttachedPolicies",
            "ListAttachedRolePolicies",
            RoleName=role_name,
        )
        inline = collect(
            iam, "list_role_policies", "PolicyNames", "ListRolePolicies", RoleName=role_name
        )
        return {
            "role": role_json(role),
            "attached_policies": clean(attached),
            "inline_policies": list(inline),
        }

    @server.tool(name="iam_role_create", annotations=DESTRUCTIVE)
    def iam_role_create(
        role_name: str,
        assume_role_policy: dict[str, Any] | str,
        description: str | None = None,
        max_session_duration: int | None = None,
        path: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Create an IAM role with a trust policy (dict or JSON) saying who may assume it;
        needs confirm=true. max_session_duration is 3,600 to 43,200 seconds. Returns the
        new role."""
        require_confirmed(settings, "CreateRole", confirm)
        check_duration("max_session_duration", max_session_duration, MIN_ROLE_SESSION_SECONDS)
        document = policy_json(assume_role_policy, "assume_role_policy")
        response = invoke(
            "CreateRole",
            clients.iam().create_role,
            RoleName=role_name,
            AssumeRolePolicyDocument=document,
            Description=description,
            MaxSessionDuration=max_session_duration,
            Path=path,
        )
        return {"ok": True, "role": role_json(response["Role"])}

    @server.tool(name="iam_role_delete", annotations=DESTRUCTIVE)
    def iam_role_delete(
        role_name: str, cascade: bool = False, confirm: bool = False
    ) -> dict[str, Any]:
        """Delete an IAM role; needs confirm=true. With cascade=true it first detaches and
        deletes the role's policies, and lists each step."""
        require_confirmed(settings, "DeleteRole", confirm)
        iam = clients.iam()
        steps = _cascade_role(iam, role_name) if cascade else []
        invoke("DeleteRole", iam.delete_role, hints=ROLE_DELETE_HINTS, RoleName=role_name)
        return {"ok": True, "role_name": role_name, "steps": [*steps, "DeleteRole"]}

    @server.tool(name="iam_role_trust_policy_update", annotations=DESTRUCTIVE)
    def iam_role_trust_policy_update(
        role_name: str, policy_document: dict[str, Any] | str, confirm: bool = False
    ) -> dict[str, Any]:
        """Replace an IAM role's trust policy (dict or JSON), changing who may assume it;
        needs confirm=true."""
        require_confirmed(settings, "UpdateAssumeRolePolicy", confirm)
        document = policy_json(policy_document)
        invoke(
            "UpdateAssumeRolePolicy",
            clients.iam().update_assume_role_policy,
            RoleName=role_name,
            PolicyDocument=document,
        )
        return {"ok": True, "role_name": role_name}

    @server.tool(name="sts_caller_identity", annotations=READ)
    def sts_caller_identity() -> dict[str, Any]:
        """Show who this server is signed in as: the account ID, user ID and ARN. Never
        returns keys."""
        response = invoke("GetCallerIdentity", clients.sts().get_caller_identity)
        return caller_identity(response, settings)

    @server.tool(name="sts_assume_role", annotations=DESTRUCTIVE)
    def sts_assume_role(
        role_arn: str,
        session_name: str,
        duration_seconds: int = 3600,
        policy: dict[str, Any] | str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Assume an IAM role and get temporary credentials; needs confirm=true.
        duration_seconds is 900 to 43,200, and an optional inline policy narrows access.
        The credentials are returned once; with WASABI_MCP_SECRETS_DIR set, they go to a new
        0600 file instead and `secrets_file` gives its path. Wasabi doesn't let the root
        account assume roles."""
        require_confirmed(settings, "AssumeRole", confirm)
        check_duration("duration_seconds", duration_seconds, MIN_SESSION_SECONDS)
        document = policy_json(policy, "policy") if policy is not None else None
        directory = _secrets.prepare(settings)
        response = invoke(
            "AssumeRole",
            clients.sts().assume_role,
            hints=ASSUME_ROLE_HINTS,
            RoleArn=role_arn,
            RoleSessionName=session_name,
            DurationSeconds=duration_seconds,
            Policy=document,
        )
        assumed_role_user = clean(response.get("AssumedRoleUser", {}))
        if directory is not None:
            return {
                "ok": True,
                "credentials": saved_credentials(
                    directory,
                    "sts_assume_role",
                    response["Credentials"],
                    f"STS session {session_name} for role {role_arn}",
                ),
                "assumed_role_user": assumed_role_user,
                "note": _secrets.SAVED_NOTE,
            }
        return {
            "ok": True,
            "credentials": temporary_credentials(response["Credentials"]),
            "assumed_role_user": assumed_role_user,
            "note": SECRET_NOTE,
        }

    @server.tool(name="sts_session_token", annotations=DESTRUCTIVE)
    def sts_session_token(
        duration_seconds: int | None = None,
        mfa_serial: str | None = None,
        mfa_code: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Get temporary session credentials for the caller, optionally MFA-authenticated
        with mfa_serial and mfa_code; needs confirm=true. duration_seconds is 900 to 43,200.
        The credentials are returned once; with WASABI_MCP_SECRETS_DIR set, they go to a new
        0600 file instead and `secrets_file` gives its path."""
        require_confirmed(settings, "GetSessionToken", confirm)
        check_duration("duration_seconds", duration_seconds, MIN_SESSION_SECONDS)
        if (mfa_serial is None) != (mfa_code is None):
            raise ToolRefused("Give both mfa_serial and mfa_code, or neither")
        directory = _secrets.prepare(settings)
        response = invoke(
            "GetSessionToken",
            clients.sts().get_session_token,
            DurationSeconds=duration_seconds,
            SerialNumber=mfa_serial,
            TokenCode=mfa_code,
        )
        if directory is not None:
            credentials = response["Credentials"]
            return {
                "ok": True,
                "credentials": saved_credentials(
                    directory,
                    "sts_session_token",
                    credentials,
                    f"STS session token {credentials['AccessKeyId']}",
                ),
                "note": _secrets.SAVED_NOTE,
            }
        return {
            "ok": True,
            "credentials": temporary_credentials(response["Credentials"]),
            "note": SECRET_NOTE,
        }

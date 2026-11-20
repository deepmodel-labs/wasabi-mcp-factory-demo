"""Attaching managed policies, inline policies and ValidatePolicy (PRD section 6.12)."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from wasabi_mcp import rest
from wasabi_mcp.safety import require_confirmed
from wasabi_mcp.tools import DESTRUCTIVE, READ, ToolContext, clean
from wasabi_mcp.tools._iam import (
    DEFAULT_MAX_ITEMS,
    PrincipalType,
    decode_policy,
    invoke,
    page_params,
    page_result,
    policy_json,
    principal_action,
    principal_key,
    principal_method,
    query_result,
)

ServiceName = Literal["iam", "sts", "s3"]


def _flag(value: bool) -> str:
    return "true" if value else "false"


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings
    clients = ctx.clients

    def principal(principal_type: PrincipalType, principal_name: str) -> dict[str, str]:
        return {principal_key(principal_type): principal_name}

    @server.tool(name="iam_policy_attach", annotations=DESTRUCTIVE)
    def iam_policy_attach(
        principal_type: PrincipalType,
        principal_name: str,
        policy_arn: str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Attach a managed policy to a user, group or role (principal_type), granting its
        permissions; needs confirm=true."""
        action = principal_action(principal_type, "Attach{}Policy")
        require_confirmed(settings, action, confirm)
        invoke(
            action,
            principal_method(clients.iam(), principal_type, "attach_{}_policy"),
            PolicyArn=policy_arn,
            **principal(principal_type, principal_name),
        )
        return {
            "ok": True,
            "principal_type": principal_type,
            "principal_name": principal_name,
            "policy_arn": policy_arn,
        }

    @server.tool(name="iam_policy_detach", annotations=DESTRUCTIVE)
    def iam_policy_detach(
        principal_type: PrincipalType,
        principal_name: str,
        policy_arn: str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Detach a managed policy from a user, group or role (principal_type), removing its
        permissions; needs confirm=true."""
        action = principal_action(principal_type, "Detach{}Policy")
        require_confirmed(settings, action, confirm)
        invoke(
            action,
            principal_method(clients.iam(), principal_type, "detach_{}_policy"),
            PolicyArn=policy_arn,
            **principal(principal_type, principal_name),
        )
        return {
            "ok": True,
            "principal_type": principal_type,
            "principal_name": principal_name,
            "policy_arn": policy_arn,
        }

    @server.tool(name="iam_attached_policy_list", annotations=READ)
    def iam_attached_policy_list(
        principal_type: PrincipalType,
        principal_name: str,
        max_items: int = DEFAULT_MAX_ITEMS,
        continuation_token: str | None = None,
    ) -> dict[str, Any]:
        """List the managed policies attached to a user, group or role, with names and
        ARNs, plus pagination."""
        response = invoke(
            principal_action(principal_type, "ListAttached{}Policies"),
            principal_method(clients.iam(), principal_type, "list_attached_{}_policies"),
            **principal(principal_type, principal_name),
            **page_params(max_items, continuation_token),
        )
        return page_result(response, "AttachedPolicies")

    @server.tool(name="iam_inline_policy_list", annotations=READ)
    def iam_inline_policy_list(
        principal_type: PrincipalType,
        principal_name: str,
        max_items: int = DEFAULT_MAX_ITEMS,
        continuation_token: str | None = None,
    ) -> dict[str, Any]:
        """List the names of the inline policies embedded in a user, group or role, plus
        pagination."""
        response = invoke(
            principal_action(principal_type, "List{}Policies"),
            principal_method(clients.iam(), principal_type, "list_{}_policies"),
            **principal(principal_type, principal_name),
            **page_params(max_items, continuation_token),
        )
        return page_result(response, "PolicyNames")

    @server.tool(name="iam_inline_policy_get", annotations=READ)
    def iam_inline_policy_get(
        principal_type: PrincipalType, principal_name: str, policy_name: str
    ) -> dict[str, Any]:
        """Show one inline policy of a user, group or role, with its document as JSON."""
        response = invoke(
            principal_action(principal_type, "Get{}Policy"),
            principal_method(clients.iam(), principal_type, "get_{}_policy"),
            PolicyName=policy_name,
            **principal(principal_type, principal_name),
        )
        return {
            "principal_type": principal_type,
            "principal_name": principal_name,
            "policy_name": response.get("PolicyName", policy_name),
            "document": decode_policy(clean(response.get("PolicyDocument"))),
        }

    @server.tool(name="iam_inline_policy_put", annotations=DESTRUCTIVE)
    def iam_inline_policy_put(
        principal_type: PrincipalType,
        principal_name: str,
        policy_name: str,
        policy_document: dict[str, Any] | str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Create or replace an inline policy (dict or JSON) on a user, group or role;
        needs confirm=true."""
        action = principal_action(principal_type, "Put{}Policy")
        require_confirmed(settings, action, confirm)
        document = policy_json(policy_document)
        invoke(
            action,
            principal_method(clients.iam(), principal_type, "put_{}_policy"),
            PolicyName=policy_name,
            PolicyDocument=document,
            **principal(principal_type, principal_name),
        )
        return {
            "ok": True,
            "principal_type": principal_type,
            "principal_name": principal_name,
            "policy_name": policy_name,
        }

    @server.tool(name="iam_inline_policy_delete", annotations=DESTRUCTIVE)
    def iam_inline_policy_delete(
        principal_type: PrincipalType,
        principal_name: str,
        policy_name: str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Delete an inline policy from a user, group or role; needs confirm=true."""
        action = principal_action(principal_type, "Delete{}Policy")
        require_confirmed(settings, action, confirm)
        invoke(
            action,
            principal_method(clients.iam(), principal_type, "delete_{}_policy"),
            PolicyName=policy_name,
            **principal(principal_type, principal_name),
        )
        return {
            "ok": True,
            "principal_type": principal_type,
            "principal_name": principal_name,
            "policy_name": policy_name,
        }

    @server.tool(name="iam_policy_validate", annotations=READ)
    def iam_policy_validate(
        policy_document: dict[str, Any] | str,
        is_principal: bool = True,
        use_action: str | None = None,
        use_resource: str | None = None,
        use_service: ServiceName | None = None,
    ) -> dict[str, Any]:
        """Lint a policy with Wasabi's ValidatePolicy and, given use_action, use_resource
        and use_service (iam, sts or s3), evaluate it for the signer. is_principal=true
        (default) for an identity policy, false for a resource policy. Returns Wasabi's lint
        and evaluation result as JSON in `validation` (ALLOW, DENY or none, and any lint
        messages). A Wasabi error is returned as an error with its code and message, never
        as a passing result. Wasabi's exact ValidatePolicy encoding is unconfirmed: the
        request follows its docs (signed form POST, PolicyDocument as JSON, IsPrincipal
        true/false) and Wasabi's error is passed back as is."""
        document = policy_json(policy_document)
        root = rest.iam_query(
            settings,
            "ValidatePolicy",
            {
                "PolicyDocument": document,
                "IsPrincipal": _flag(is_principal),
                "UseAction": use_action,
                "UseResource": use_resource,
                "UseService": use_service,
            },
        )
        return {"validation": query_result(root, "ValidatePolicy")}

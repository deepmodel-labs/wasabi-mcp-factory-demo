"""WACM Connect members: users of a sub-account (PRD section 6.14)."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from wasabi_mcp.safety import ToolRefused, require_confirmed
from wasabi_mcp.tools import DESTRUCTIVE, READ, ToolContext
from wasabi_mcp.tools._wacm import call, compact, page_result, result


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings

    @server.tool(name="wacm_member_list", annotations=READ)
    def wacm_member_list(
        page: int | None = None,
        size: int | None = None,
        id: int | None = None,
        status: str | None = None,
        username: str | None = None,
        sub_account_id: int | None = None,
    ) -> dict[str, Any]:
        """List WACM Connect members, optionally filtered by ID, status, username or
        sub-account. Returns one page (pages start at 0)."""
        params = {
            "page": page,
            "size": size,
            "id": id,
            "status": status,
            "username": username,
            "subAccountId": sub_account_id,
        }
        return page_result(call(settings, "GET", "/v1/members", params=params), page, size)

    @server.tool(name="wacm_member_get", annotations=READ)
    def wacm_member_get(member_id: int) -> Any:
        """Get one WACM Connect member by ID."""
        return result(call(settings, "GET", f"/v1/members/{member_id}"))

    @server.tool(name="wacm_member_create", annotations=DESTRUCTIVE)
    def wacm_member_create(
        first_name: str,
        last_name: str,
        email: str,
        password: str,
        member_role: str,
        username: str | None = None,
        password_reset_required: bool | None = None,
        confirm: bool = False,
    ) -> Any:
        """Create a WACM Connect member with the given role, granting them access. Returns
        the new member (never its password). Needs confirm=true."""
        require_confirmed(settings, "CreateMember", confirm)
        body = compact(
            firstName=first_name,
            lastName=last_name,
            email=email,
            password=password,
            memberRole=member_role,
            username=username,
            passwordResetRequired=password_reset_required,
        )
        return result(call(settings, "POST", "/v1/members", body=body))

    @server.tool(name="wacm_member_update", annotations=DESTRUCTIVE)
    def wacm_member_update(
        member_id: int,
        member_role: str | None = None,
        reset_mfa: bool | None = None,
        password: str | None = None,
        active: bool | None = None,
        reinstate: bool | None = None,
        confirm: bool = False,
    ) -> Any:
        """Change a WACM Connect member's role, reset their MFA, set a password, enable or
        disable them, or reinstate them. Only the fields given are sent. Needs
        confirm=true."""
        action = "UpdateMember"
        require_confirmed(settings, action, confirm)
        body = compact(
            memberRole=member_role,
            resetMFA=reset_mfa,
            password=password,
            active=active,
            reinstate=reinstate,
        )
        if not body:
            raise ToolRefused(f"{action} refused: give at least one field to change")
        return result(call(settings, "PUT", f"/v1/members/{member_id}", body=body))

    @server.tool(name="wacm_member_delete", annotations=DESTRUCTIVE)
    def wacm_member_delete(member_id: int, confirm: bool = False) -> dict[str, Any]:
        """Delete a WACM Connect member. Needs confirm=true."""
        require_confirmed(settings, "DeleteMember", confirm)
        call(settings, "DELETE", f"/v1/members/{member_id}")
        return {"ok": True, "member_id": member_id}

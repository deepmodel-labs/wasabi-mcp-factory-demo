"""WACM Connect channel accounts and their users (PRD section 6.14)."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from wasabi_mcp.safety import ToolRefused, require_confirmed, require_writable
from wasabi_mcp.tools import DESTRUCTIVE, READ, WRITE, ToolContext
from wasabi_mcp.tools._wacm import call, compact, page_result, result

QuotaType = Literal["Soft", "Hard"]


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings

    @server.tool(name="wacm_channel_account_list", annotations=READ)
    def wacm_channel_account_list(
        page: int | None = None, size: int | None = None
    ) -> dict[str, Any]:
        """List WACM Connect channel accounts. Returns one page (pages start at 0);
        next_token is the next page number."""
        params = {"page": page, "size": size}
        return page_result(call(settings, "GET", "/v1/channel-accounts", params=params), page, size)

    @server.tool(name="wacm_channel_account_get", annotations=READ)
    def wacm_channel_account_get(channel_account_id: int) -> Any:
        """Get one WACM Connect channel account by ID."""
        return result(call(settings, "GET", f"/v1/channel-accounts/{channel_account_id}"))

    @server.tool(name="wacm_channel_account_create", annotations=WRITE)
    def wacm_channel_account_create(
        name: str,
        contact_email: str,
        purchased_storage: float | None = None,
        storage_quota_type: QuotaType | None = None,
    ) -> Any:
        """Create a WACM Connect channel account with an optional purchased storage amount
        (TB) and Soft or Hard quota. Returns the new channel account."""
        require_writable(settings, "CreateChannelAccount")
        body = compact(
            name=name,
            contactEmail=contact_email,
            purchasedStorage=purchased_storage,
            storageQuotaType=storage_quota_type,
        )
        return result(call(settings, "POST", "/v1/channel-accounts", body=body))

    @server.tool(name="wacm_channel_account_update", annotations=WRITE)
    def wacm_channel_account_update(
        channel_account_id: int,
        name: str | None = None,
        contact_email: str | None = None,
        purchased_storage: float | None = None,
        storage_quota_type: QuotaType | None = None,
    ) -> Any:
        """Update a WACM Connect channel account's name, contact email, purchased storage
        (TB) or quota type. Only the fields given are sent. Returns the updated account."""
        require_writable(settings, "UpdateChannelAccount")
        body = compact(
            name=name,
            contactEmail=contact_email,
            purchasedStorage=purchased_storage,
            storageQuotaType=storage_quota_type,
        )
        if not body:
            raise ToolRefused("UpdateChannelAccount refused: give at least one field to change")
        path = f"/v1/channel-accounts/{channel_account_id}"
        return result(call(settings, "PUT", path, body=body))

    @server.tool(name="wacm_channel_account_delete", annotations=DESTRUCTIVE)
    def wacm_channel_account_delete(channel_account_id: int, confirm: bool = False) -> Any:
        """Delete a WACM Connect channel account. This also unlinks all of its
        sub-accounts. Needs confirm=true."""
        require_confirmed(settings, "DeleteChannelAccount", confirm)
        call(settings, "DELETE", f"/v1/channel-accounts/{channel_account_id}")
        return {"ok": True, "channel_account_id": channel_account_id}

    @server.tool(name="wacm_channel_user_create", annotations=WRITE)
    def wacm_channel_user_create(
        channel_account_id: int,
        email: str,
        first_name: str,
        last_name: str,
        role: str,
        username: str | None = None,
        password: str | None = None,
    ) -> Any:
        """Create a user on a WACM Connect channel account with the given role. Returns
        the new user (never its password)."""
        require_writable(settings, "CreateChannelUser")
        body = compact(
            channelAccountId=channel_account_id,
            email=email,
            firstName=first_name,
            lastName=last_name,
            role=role,
            username=username,
            password=password,
        )
        return result(call(settings, "POST", "/v1/channel-accounts/users", body=body))

    @server.tool(name="wacm_channel_user_update", annotations=DESTRUCTIVE)
    def wacm_channel_user_update(
        user_id: int,
        role: str | None = None,
        active: bool | None = None,
        reset_mfa: bool | None = None,
        password: str | None = None,
        confirm: bool = False,
    ) -> Any:
        """Change a channel account user's role, enable or disable them, reset their MFA
        or set a new password. Changes access, so it needs confirm=true."""
        require_confirmed(settings, "UpdateChannelUser", confirm)
        body = compact(role=role, active=active, resetMFA=reset_mfa, password=password)
        path = f"/v1/channel-accounts/users/{user_id}"
        return result(call(settings, "PUT", path, body=body))

    @server.tool(name="wacm_channel_user_delete", annotations=DESTRUCTIVE)
    def wacm_channel_user_delete(user_id: int, confirm: bool = False) -> Any:
        """Delete a channel account user. Needs confirm=true."""
        require_confirmed(settings, "DeleteChannelUser", confirm)
        call(settings, "DELETE", f"/v1/channel-accounts/users/{user_id}")
        return {"ok": True, "user_id": user_id}

"""IAM access keys (PRD section 6.11)."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from wasabi_mcp.safety import ToolRefused, require_confirmed
from wasabi_mcp.tools import DESTRUCTIVE, READ, ToolContext, _secrets, clean
from wasabi_mcp.tools._iam import DEFAULT_MAX_ITEMS, SECRET_NOTE, invoke, page_params, page_result

# Wasabi's ListAccessKeys accepts UserName=* to list every user's keys.
ALL_USERS = "*"

_KEY_FIELDS = ("UserName", "AccessKeyId", "Status", "CreateDate")


def key_metadata(key: dict[str, Any]) -> dict[str, Any]:
    """A key's user, ID, status and created date. Anything else (secrets) is dropped.

    Root's keys come back without a user name; they get a blank one.
    """
    fields = {field: key.get(field) for field in _KEY_FIELDS if field in key}
    return clean({**fields, "UserName": key.get("UserName") or ""})


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings
    clients = ctx.clients

    @server.tool(name="iam_access_key_list", annotations=READ)
    def iam_access_key_list(
        user_name: str | None = None,
        all_users: bool = False,
        max_items: int = DEFAULT_MAX_ITEMS,
        continuation_token: str | None = None,
    ) -> dict[str, Any]:
        """List access keys for a user (default: the caller), or for every user with
        all_users=true (root's keys show a blank user name). Returns key IDs, status and
        created dates, never secrets."""
        if all_users and user_name:
            raise ToolRefused("Give either user_name or all_users=true, not both")
        response = invoke(
            "ListAccessKeys",
            clients.iam().list_access_keys,
            UserName=ALL_USERS if all_users else user_name,
            **page_params(max_items, continuation_token),
        )
        return page_result(response, "AccessKeyMetadata", key_metadata)

    @server.tool(name="iam_access_key_create", annotations=DESTRUCTIVE)
    def iam_access_key_create(
        user_name: str | None = None, confirm: bool = False
    ) -> dict[str, Any]:
        """Create an access key for a user (default: the caller); needs confirm=true.
        Returns `access_key_id` and `secret_access_key` once: the secret can't be retrieved
        again. With WASABI_MCP_SECRETS_DIR set, the secret goes to a new 0600 file instead
        and `secrets_file` gives its path. Users can have at most two keys."""
        require_confirmed(settings, "CreateAccessKey", confirm)
        directory = _secrets.prepare(settings)
        response = invoke("CreateAccessKey", clients.iam().create_access_key, UserName=user_name)
        key = response["AccessKey"]
        if directory is not None:
            details = {
                "user_name": key.get("UserName"),
                "access_key_id": key["AccessKeyId"],
                "status": key.get("Status"),
                "create_date": clean(key.get("CreateDate")),
            }
            path = _secrets.write(
                directory,
                "iam_access_key_create",
                {**details, "secret_access_key": key["SecretAccessKey"]},
                f"Access key {key['AccessKeyId']}",
            )
            return {"ok": True, **details, "secrets_file": path, "note": _secrets.SAVED_NOTE}
        return {
            "ok": True,
            "user_name": key.get("UserName"),
            "access_key_id": key["AccessKeyId"],
            "secret_access_key": key["SecretAccessKey"],
            "status": key.get("Status"),
            "create_date": clean(key.get("CreateDate")),
            "note": SECRET_NOTE,
        }

    @server.tool(name="iam_access_key_update", annotations=DESTRUCTIVE)
    def iam_access_key_update(
        access_key_id: str,
        active: bool,
        user_name: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Activate (active=true) or deactivate (active=false) an access key; needs
        confirm=true. A deactivated key stops working at once but can be reactivated."""
        require_confirmed(settings, "UpdateAccessKey", confirm)
        status = "Active" if active else "Inactive"
        invoke(
            "UpdateAccessKey",
            clients.iam().update_access_key,
            UserName=user_name,
            AccessKeyId=access_key_id,
            Status=status,
        )
        return {"ok": True, "access_key_id": access_key_id, "status": status}

    @server.tool(name="iam_access_key_delete", annotations=DESTRUCTIVE)
    def iam_access_key_delete(
        access_key_id: str, user_name: str | None = None, confirm: bool = False
    ) -> dict[str, Any]:
        """Delete an access key permanently; needs confirm=true. Anything still using the
        key stops working."""
        require_confirmed(settings, "DeleteAccessKey", confirm)
        invoke(
            "DeleteAccessKey",
            clients.iam().delete_access_key,
            UserName=user_name,
            AccessKeyId=access_key_id,
        )
        return {"ok": True, "access_key_id": access_key_id}

    @server.tool(name="iam_access_key_last_used", annotations=READ)
    def iam_access_key_last_used(access_key_id: str) -> dict[str, Any]:
        """Show when an access key was last used, and for which service and region.
        Returns the key's user name and last-used details."""
        response = invoke(
            "GetAccessKeyLastUsed",
            clients.iam().get_access_key_last_used,
            AccessKeyId=access_key_id,
        )
        return {
            "access_key_id": access_key_id,
            "user_name": response.get("UserName"),
            "last_used": clean(response.get("AccessKeyLastUsed", {})),
        }

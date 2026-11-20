"""WACM Connect sub-accounts (PRD section 6.14)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from wasabi_mcp.safety import ToolRefused, require_confirmed
from wasabi_mcp.tools import DESTRUCTIVE, READ, ToolContext, _secrets
from wasabi_mcp.tools._wacm import call, compact, page_result, result

QuotaType = Literal["Soft", "Hard"]
Status = Literal["ON_TRIAL", "PAID_ACCOUNT", "SUSPENDED", "DEACTIVATED"]

NAME_LENGTH = (3, 100)
# Field names (lower-cased, without ``_``) WACM may use for the new account's keys.
_ACCESS_KEY_FIELDS = frozenset({"accesskey", "accesskeyid"})
_SECRET_KEY_FIELDS = frozenset({"secretkey", "secretaccesskey"})


def _find(value: Any, fields: frozenset[str]) -> str | None:
    """The first string under any of ``fields``, searching nested objects."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower().replace("_", "") in fields and isinstance(item, str):
                return item
        for item in value.values():
            found = _find(item, fields)
            if found is not None:
                return found
    if isinstance(value, list):
        return next((f for f in (_find(i, fields) for i in value) if f is not None), None)
    return None


def _check_quota(quota_type: str | None, purchased_tb: float | None, action: str) -> None:
    if quota_type == "Hard" and purchased_tb is None:
        raise ToolRefused(f"{action} refused: storage_quota_type='Hard' needs purchased_storage_tb")


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings

    @server.tool(name="wacm_sub_account_list", annotations=READ)
    def wacm_sub_account_list(
        page: int | None = None,
        size: int | None = None,
        id: int | None = None,
        name: str | None = None,
        control_account_id: int | None = None,
        governance_account_id: int | None = None,
        status: Status | None = None,
        include_deleted: bool | None = None,
    ) -> dict[str, Any]:
        """List WACM Connect sub-accounts with their quota settings (purchasedStorageTB,
        storageQuotaType), optionally filtered. Returns one page (pages start at 0); keys
        are never requested or returned."""
        params = {
            "page": page,
            "size": size,
            "id": id,
            "name": name,
            "controlAccountId": control_account_id,
            "governanceAccountId": governance_account_id,
            "status": status,
            "includeDeleted": include_deleted,
        }
        return page_result(call(settings, "GET", "/v1/sub-accounts", params=params), page, size)

    @server.tool(name="wacm_sub_account_get", annotations=READ)
    def wacm_sub_account_get(sub_account_id: int) -> Any:
        """Get one WACM Connect sub-account by ID, including its quota settings. Its keys
        are never returned."""
        return result(call(settings, "GET", f"/v1/sub-accounts/{sub_account_id}"))

    @server.tool(name="wacm_sub_account_create", annotations=DESTRUCTIVE)
    def wacm_sub_account_create(
        name: str,
        wasabi_account_email: str,
        password: str | None = None,
        send_welcome_email: bool | None = None,
        control_account_id: int | None = None,
        channel_account_id: int | None = None,
        is_trial: bool | None = None,
        trial_quota_tb: float | None = None,
        trial_days: int | None = None,
        purchased_storage_tb: float | None = None,
        storage_quota_type: QuotaType | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Create a WACM Connect sub-account (a new Wasabi account) with an optional Soft or
        Hard storage quota; Hard needs purchased_storage_tb. Give a password or
        send_welcome_email=true. Returns the account and its new access_key_id and
        secret_access_key, shown only this once; with WASABI_MCP_SECRETS_DIR set, the keys
        go to a new 0600 file instead and `secrets_file` gives its path. Needs
        confirm=true."""
        action = "CreateSubAccount"
        require_confirmed(settings, action, confirm)
        low, high = NAME_LENGTH
        if not low <= len(name) <= high:
            raise ToolRefused(f"{action} refused: name must be {low}-{high} characters")
        if password is None and send_welcome_email is not True:
            raise ToolRefused(f"{action} refused: give a password or send_welcome_email=true")
        _check_quota(storage_quota_type, purchased_storage_tb, action)
        directory = _secrets.prepare(settings)
        body = compact(
            name=name,
            wasabiAccountEmail=wasabi_account_email,
            password=password,
            sendWelcomeEmail=send_welcome_email,
            controlAccountId=control_account_id,
            channelAccountId=channel_account_id,
            isTrial=is_trial,
            trialQuotaTB=trial_quota_tb,
            trialDays=trial_days,
            purchasedStorageTB=purchased_storage_tb,
            storageQuotaType=storage_quota_type,
        )
        response = call(settings, "POST", "/v1/sub-accounts", body=body)
        access_key_id = _find(response, _ACCESS_KEY_FIELDS)
        secret_access_key = _find(response, _SECRET_KEY_FIELDS)
        if directory is not None:
            path = None
            if secret_access_key is not None:
                path = _secrets.write(
                    directory,
                    "wacm_sub_account_create",
                    {"access_key_id": access_key_id, "secret_access_key": secret_access_key},
                    f"Sub-account {name} (access key {access_key_id})",
                )
            return {
                "sub_account": result(response),
                "access_key_id": access_key_id,
                "secrets_file": path,
            }
        return {
            "sub_account": result(response),
            "access_key_id": access_key_id,
            "secret_access_key": secret_access_key,
        }

    @server.tool(name="wacm_sub_account_update", annotations=DESTRUCTIVE)
    def wacm_sub_account_update(
        sub_account_id: int,
        active: bool | None = None,
        trial_to_paid: bool | None = None,
        purchased_storage_tb: float | None = None,
        storage_quota_type: QuotaType | None = None,
        reset_keys: bool | None = None,
        reset_mfa: bool | None = None,
        confirm: bool = False,
    ) -> Any:
        """Partly update a WACM Connect sub-account: enable or disable it (active), convert
        a trial to paid, change its quota, reset its keys (new keys aren't returned) or
        reset its MFA. Only the fields given are sent. Needs confirm=true."""
        action = "UpdateSubAccount"
        require_confirmed(settings, action, confirm)
        _check_quota(storage_quota_type, purchased_storage_tb, action)
        body = compact(
            active=active,
            trialToPaid=trial_to_paid,
            purchasedStorageTB=purchased_storage_tb,
            storageQuotaType=storage_quota_type,
            resetKeys=reset_keys,
            resetMFA=reset_mfa,
        )
        if not body:
            raise ToolRefused(f"{action} refused: give at least one field to change")
        path = f"/v1/sub-accounts/{sub_account_id}"
        return result(call(settings, "PATCH", path, body=body))

    @server.tool(name="wacm_sub_account_delete", annotations=DESTRUCTIVE)
    def wacm_sub_account_delete(sub_account_id: int, confirm: bool = False) -> dict[str, Any]:
        """Delete a WACM Connect sub-account and the Wasabi account behind it. Needs
        confirm=true."""
        require_confirmed(settings, "DeleteSubAccount", confirm)
        call(settings, "DELETE", f"/v1/sub-accounts/{sub_account_id}")
        return {"ok": True, "sub_account_id": sub_account_id}

    @server.tool(name="wacm_sub_account_bucket_list", annotations=READ)
    def wacm_sub_account_bucket_list(
        sub_account_id: int,
        page: int | None = None,
        size: int | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        latest: bool | None = None,
        name: str | None = None,
        bucket_number: int | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        """List a sub-account's buckets with their usage, optionally filtered by
        from_date/to_date (YYYY-MM-DD), latest, name, bucket number or region. Returns one
        page (pages start at 0)."""
        params = {
            "page": page,
            "size": size,
            "from": from_date,
            "to": to_date,
            "latest": latest,
            "name": name,
            "bucketNumber": bucket_number,
            "region": region,
        }
        path = f"/v1/sub-accounts/{sub_account_id}/buckets"
        return page_result(call(settings, "GET", path, params=params), page, size)

"""WACM Connect standalone accounts and control accounts (PRD section 6.14)."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from wasabi_mcp.safety import require_writable
from wasabi_mcp.tools import READ, WRITE, ToolContext
from wasabi_mcp.tools._wacm import call, compact, page_result, result


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings

    @server.tool(name="wacm_standalone_account_list", annotations=READ)
    def wacm_standalone_account_list(
        page: int | None = None,
        size: int | None = None,
        id: int | None = None,
        name: str | None = None,
        email: str | None = None,
        status: str | None = None,
        wasabi_account_number: int | None = None,
        partner_name: str | None = None,
        company_name: str | None = None,
    ) -> dict[str, Any]:
        """List WACM Connect standalone accounts, optionally filtered. Returns one page
        (pages start at 0); next_token is the next page number."""
        params = {
            "page": page,
            "size": size,
            "id": id,
            "name": name,
            "email": email,
            "status": status,
            "wasabiAccountNumber": wasabi_account_number,
            "partnerName": partner_name,
            "companyName": company_name,
        }
        return page_result(call(settings, "GET", "/v1/accounts", params=params), page, size)

    @server.tool(name="wacm_standalone_account_create", annotations=WRITE)
    def wacm_standalone_account_create(
        first_name: str,
        last_name: str,
        email: str,
        country: str,
        storage_amount: str,
        company_name: str | None = None,
        phone_number: str | None = None,
    ) -> dict[str, Any]:
        """Create a WACM Connect standalone account. country comes from wacm_country_list
        and storage_amount is a band from wacm_storage_amount_list. Returns the new
        account."""
        require_writable(settings, "CreateStandaloneAccount")
        body = compact(
            firstName=first_name,
            lastName=last_name,
            email=email,
            country=country,
            storageAmount=storage_amount,
            companyName=company_name,
            phoneNumber=phone_number,
        )
        return result(call(settings, "POST", "/v1/accounts", body=body))

    @server.tool(name="wacm_storage_amount_list", annotations=READ)
    def wacm_storage_amount_list() -> Any:
        """List the storage amount bands accepted by wacm_standalone_account_create."""
        return result(call(settings, "GET", "/v1/accounts/storage-amounts"))

    @server.tool(name="wacm_country_list", annotations=READ)
    def wacm_country_list() -> Any:
        """List the countries accepted by wacm_standalone_account_create."""
        return result(call(settings, "GET", "/v1/accounts/countries"))

    @server.tool(name="wacm_control_account_list", annotations=READ)
    def wacm_control_account_list(
        page: int | None = None, size: int | None = None
    ) -> dict[str, Any]:
        """List WACM Connect control accounts. Returns one page (pages start at 0); API
        keys are never requested or returned."""
        params = {"page": page, "size": size}
        return page_result(call(settings, "GET", "/v1/control-accounts", params=params), page, size)

    @server.tool(name="wacm_control_account_get", annotations=READ)
    def wacm_control_account_get(control_account_id: int) -> Any:
        """Get one WACM Connect control account by ID. Its API key is never returned."""
        return result(call(settings, "GET", f"/v1/control-accounts/{control_account_id}"))

    @server.tool(name="wacm_control_account_usage_list", annotations=READ)
    def wacm_control_account_usage_list(
        page: int | None = None,
        size: int | None = None,
        governance_account_id: int | None = None,
        control_account_id: int | None = None,
        sub_account_id: int | None = None,
        wasabi_account_number: int | None = None,
        latest: bool | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        """List control account usage records (storage and transfer per day), optionally
        filtered by account, latest only, or a from_date/to_date range (YYYY-MM-DD).
        Returns one page (pages start at 0)."""
        params = {
            "page": page,
            "size": size,
            "governanceAccountId": governance_account_id,
            "controlAccountId": control_account_id,
            "subAccountId": sub_account_id,
            "wasabiAccountNumber": wasabi_account_number,
            "latest": latest,
            "from": from_date,
            "to": to_date,
        }
        return page_result(
            call(settings, "GET", "/v1/control-accounts/usages", params=params), page, size
        )

    @server.tool(name="wacm_control_account_usage_get", annotations=READ)
    def wacm_control_account_usage_get(utilization_id: int) -> Any:
        """Get one control account usage record by its utilization ID."""
        return result(call(settings, "GET", f"/v1/control-accounts/usages/{utilization_id}"))

    @server.tool(name="wacm_control_account_bucket_list", annotations=READ)
    def wacm_control_account_bucket_list(
        control_account_id: int,
        page: int | None = None,
        size: int | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        latest: bool | None = None,
        name: str | None = None,
        bucket_number: int | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        """List a control account's buckets with their usage, optionally filtered by
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
        path = f"/v1/control-accounts/{control_account_id}/buckets"
        return page_result(call(settings, "GET", path, params=params), page, size)

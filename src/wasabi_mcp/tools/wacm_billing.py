"""WACM Connect invoices and usages (PRD section 6.14)."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from wasabi_mcp.tools import READ, ToolContext
from wasabi_mcp.tools._wacm import call, page_result, result


def _filters(
    page: int | None,
    size: int | None,
    governance_account_id: int | None,
    control_account_id: int | None,
    sub_account_id: int | None,
    invoice_id: int | None,
    wasabi_account_number: int | None,
    latest: bool | None,
    from_date: str | None,
    to_date: str | None,
) -> dict[str, Any]:
    return {
        "page": page,
        "size": size,
        "governanceAccountId": governance_account_id,
        "controlAccountId": control_account_id,
        "subAccountId": sub_account_id,
        "invoiceId": invoice_id,
        "wasabiAccountNumber": wasabi_account_number,
        "latest": latest,
        "from": from_date,
        "to": to_date,
    }


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings

    @server.tool(name="wacm_invoice_list", annotations=READ)
    def wacm_invoice_list(
        page: int | None = None,
        size: int | None = None,
        governance_account_id: int | None = None,
        control_account_id: int | None = None,
        sub_account_id: int | None = None,
        invoice_id: int | None = None,
        wasabi_account_number: int | None = None,
        latest: bool | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        """List WACM Connect invoices, optionally filtered by governance, control or
        sub-account, invoice ID, Wasabi account number, latest, or a from_date/to_date
        range (YYYY-MM-DD). Returns one page (pages start at 0)."""
        params = _filters(
            page,
            size,
            governance_account_id,
            control_account_id,
            sub_account_id,
            invoice_id,
            wasabi_account_number,
            latest,
            from_date,
            to_date,
        )
        return page_result(call(settings, "GET", "/v1/invoices", params=params), page, size)

    @server.tool(name="wacm_invoice_get", annotations=READ)
    def wacm_invoice_get(invoice_id: int) -> Any:
        """Get one WACM Connect invoice by ID."""
        return result(call(settings, "GET", f"/v1/invoices/{invoice_id}"))

    @server.tool(name="wacm_usage_list", annotations=READ)
    def wacm_usage_list(
        page: int | None = None,
        size: int | None = None,
        governance_account_id: int | None = None,
        control_account_id: int | None = None,
        sub_account_id: int | None = None,
        invoice_id: int | None = None,
        wasabi_account_number: int | None = None,
        latest: bool | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        """List WACM Connect usage records (storage and transfer per sub-account), with
        the same filters as wacm_invoice_list; latest=true gives each account's most recent
        record. Compare with wacm_sub_account_list quotas to find accounts near quota.
        Returns one page (pages start at 0)."""
        params = _filters(
            page,
            size,
            governance_account_id,
            control_account_id,
            sub_account_id,
            invoice_id,
            wasabi_account_number,
            latest,
            from_date,
            to_date,
        )
        return page_result(call(settings, "GET", "/v1/usages", params=params), page, size)

    @server.tool(name="wacm_usage_get", annotations=READ)
    def wacm_usage_get(utilization_id: int) -> Any:
        """Get one WACM Connect usage record by its utilization ID."""
        return result(call(settings, "GET", f"/v1/usages/{utilization_id}"))

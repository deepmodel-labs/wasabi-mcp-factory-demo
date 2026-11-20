from dataclasses import replace

import pytest
import respx

from tests.tools.wacm_support import assert_refused
from wasabi_mcp.server import build_server

MISSING = "WASABI_WACM_USERNAME and WASABI_WACM_API_KEY"

# Valid arguments for every WACM tool. Destructive tools also get confirm=true below.
ARGUMENTS = {
    "wacm_standalone_account_list": {},
    "wacm_standalone_account_create": {
        "first_name": "A",
        "last_name": "B",
        "email": "a@example.com",
        "country": "Canada",
        "storage_amount": "1-5 TB",
    },
    "wacm_storage_amount_list": {},
    "wacm_country_list": {},
    "wacm_control_account_list": {},
    "wacm_control_account_get": {"control_account_id": 1},
    "wacm_control_account_usage_list": {},
    "wacm_control_account_usage_get": {"utilization_id": 1},
    "wacm_control_account_bucket_list": {"control_account_id": 1},
    "wacm_channel_account_list": {},
    "wacm_channel_account_get": {"channel_account_id": 1},
    "wacm_channel_account_create": {"name": "east", "contact_email": "c@example.com"},
    "wacm_channel_account_update": {"channel_account_id": 1, "name": "west"},
    "wacm_channel_account_delete": {"channel_account_id": 1},
    "wacm_channel_user_create": {
        "channel_account_id": 1,
        "email": "u@example.com",
        "first_name": "U",
        "last_name": "V",
        "role": "ADMIN",
    },
    "wacm_channel_user_update": {"user_id": 1, "active": False},
    "wacm_channel_user_delete": {"user_id": 1},
    "wacm_sub_account_list": {},
    "wacm_sub_account_get": {"sub_account_id": 1},
    "wacm_sub_account_create": {
        "name": "tenant",
        "wasabi_account_email": "t@example.com",
        "send_welcome_email": True,
        "purchased_storage_tb": 10,
        "storage_quota_type": "Hard",
    },
    "wacm_sub_account_update": {"sub_account_id": 1, "active": False},
    "wacm_sub_account_delete": {"sub_account_id": 1},
    "wacm_sub_account_bucket_list": {"sub_account_id": 1},
    "wacm_member_list": {},
    "wacm_member_get": {"member_id": 1},
    "wacm_member_create": {
        "first_name": "M",
        "last_name": "N",
        "email": "m@example.com",
        "password": "Passw0rd!",
        "member_role": "ADMIN",
    },
    "wacm_member_update": {"member_id": 1, "active": False},
    "wacm_member_delete": {"member_id": 1},
    "wacm_invoice_list": {},
    "wacm_invoice_get": {"invoice_id": 1},
    "wacm_usage_list": {},
    "wacm_usage_get": {"utilization_id": 1},
}


@pytest.fixture
def unconfigured_server(rest_settings):
    """Writes allowed, WACM endpoint set, but no WACM username or API key."""
    return build_server(replace(rest_settings, wacm_username=None, wacm_api_key=None))


async def test_the_table_covers_every_wacm_tool(unconfigured_server):
    tools = {t.name for t in await unconfigured_server.list_tools() if t.name.startswith("wacm_")}
    assert tools == set(ARGUMENTS)
    assert len(tools) == 32


@pytest.mark.parametrize("tool", sorted(ARGUMENTS))
@respx.mock
async def test_every_wacm_tool_names_the_missing_variables(unconfigured_server, tool):
    route = respx.route(host="wacm.wasabi.test")
    tools = {t.name: t for t in await unconfigured_server.list_tools()}
    confirm = {"confirm": True} if tools[tool].annotations.destructive_hint else {}
    await assert_refused(unconfigured_server, tool, MISSING, **ARGUMENTS[tool], **confirm)
    assert route.call_count == 0

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from wasabi_mcp.config import Settings
from wasabi_mcp.safety import ToolRefused, require_confirmed, require_writable

READ_ONLY = Settings(read_only=True)
WRITABLE = Settings(read_only=False)


def test_tool_refused_reaches_clients_as_a_tool_error():
    assert issubclass(ToolRefused, ToolError)


def test_require_writable_refuses_in_read_only_mode_and_says_how_to_enable_writes():
    with pytest.raises(ToolRefused, match="CreateBucket") as exc:
        require_writable(READ_ONLY, "CreateBucket")
    assert "WASABI_MCP_READ_ONLY=false" in str(exc.value)


def test_require_writable_allows_writes():
    require_writable(WRITABLE, "CreateBucket")


def test_require_confirmed_checks_read_only_first():
    with pytest.raises(ToolRefused, match="WASABI_MCP_READ_ONLY=false"):
        require_confirmed(READ_ONLY, "DeleteBucket", True)


def test_require_confirmed_refuses_without_confirm():
    with pytest.raises(ToolRefused, match="confirm=true"):
        require_confirmed(WRITABLE, "DeleteBucket", False)


def test_require_confirmed_allows_confirmed_calls():
    require_confirmed(WRITABLE, "DeleteBucket", True)

"""An MCP server for Wasabi cloud storage."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("wasabi-mcp")
except PackageNotFoundError:  # pragma: no cover - only when run from an uninstalled tree
    __version__ = "0.0.0"

"""Builds the MCP server and runs it from the ``wasabi-mcp`` command."""

from __future__ import annotations

import argparse
import hmac
import importlib
import ipaddress
import os
from collections.abc import Iterable, Sequence

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from wasabi_mcp import __version__
from wasabi_mcp.clients import Clients
from wasabi_mcp.config import Settings
from wasabi_mcp.tools import ToolContext, discover_groups

SERVER_NAME = "wasabi-mcp"

TOKEN_ENV = "WASABI_MCP_HTTP_TOKEN"
ALLOWED_HOSTS_ENV = "WASABI_MCP_ALLOWED_HOSTS"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

INSTRUCTIONS = """\
Tools for Wasabi cloud storage: S3 buckets and objects, IAM, STS, usage stats and \
WACM Connect account governance.

The server is read-only by default: tools that write (W) or destroy (D) refuse until \
the operator sets WASABI_MCP_READ_ONLY=false. Destructive tools also need confirm=true \
on each call. Call server_info to see the current mode, region and configured services."""


def build_server(settings: Settings | None = None) -> MCPServer:
    """An ``MCPServer`` with every discovered tool group registered."""
    resolved = settings if settings is not None else Settings.from_env()
    ctx = ToolContext(settings=resolved, clients=Clients(resolved))
    server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS, version=__version__)
    for name in discover_groups():
        importlib.import_module(f"wasabi_mcp.tools.{name}").register(server, ctx)
    return server


def _ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """``host`` as an IP address (brackets around IPv6 allowed), or ``None`` for a name."""
    try:
        return ipaddress.ip_address(host.removeprefix("[").removesuffix("]"))
    except ValueError:
        return None


def is_loopback_host(host: str) -> bool:
    """True for ``localhost`` or a loopback IP literal (``127.0.0.0/8``, ``::1``)."""
    if host.strip().lower() == "localhost":
        return True
    ip = _ip(host.strip())
    return ip is not None and ip.is_loopback


def _normalise_host(entry: str) -> tuple[str, ...]:
    """Host-header patterns for one entry, in the SDK's form (exact, or ``base:*``).

    An entry with a port (or an operator's ``:*``) is kept as given. A bare host allows both
    no port (proxies on 80/443) and any port. A bare IPv6 address is bracketed.
    """
    host = entry.strip().lower()
    if host.endswith(":*") or "]:" in host:
        return (host,)
    if host.startswith("["):
        return (host, f"{host}:*")
    if isinstance(_ip(host), ipaddress.IPv6Address):
        return (f"[{host}]", f"[{host}]:*")
    if ":" in host:
        return (host,)
    return (host, f"{host}:*")


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def split_hosts(value: str | None) -> list[str]:
    """Comma-separated host entries, trimmed, with empty entries dropped."""
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def allowed_hosts(bound_host: str, extra: Sequence[str] = ()) -> list[str]:
    """Host-header patterns allowed for a server bound to ``bound_host``.

    The bound host, the loopback names and every ``extra`` entry, de-duplicated in order.
    """
    entries = _unique(e.strip() for e in (bound_host, *LOOPBACK_HOSTS, *extra) if e.strip())
    return _unique(pattern for entry in entries for pattern in _normalise_host(entry))


def allowed_origins(hosts: Sequence[str]) -> list[str]:
    """``http://`` and ``https://`` origins for each allowed host pattern."""
    return [f"{scheme}://{host}" for host in hosts for scheme in ("http", "https")]


class BearerTokenMiddleware:
    """Rejects every HTTP request without ``Authorization: Bearer <token>`` with a 401.

    Non-HTTP scopes (``lifespan``) pass through. The token is never logged or echoed.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._token = token.encode()

    def _authorised(self, scope: Scope) -> bool:
        scheme, _, credentials = Headers(scope=scope).get("authorization", "").partition(" ")
        return scheme.lower() == "bearer" and hmac.compare_digest(
            credentials.strip().encode(), self._token
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self._authorised(scope):
            await self.app(scope, receive, send)
            return
        response = PlainTextResponse(
            "Unauthorized: a valid bearer token is required",
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)


def http_app(server: MCPServer, host: str, token: str | None, hosts: Sequence[str]) -> ASGIApp:
    """The streamable-HTTP app with DNS-rebinding protection, behind a bearer token if given.

    ``hosts`` are Host-header patterns, as returned by ``allowed_hosts``.
    """
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(hosts),
        allowed_origins=allowed_origins(hosts),
    )
    app = server.streamable_http_app(transport_security=security, host=host)
    return BearerTokenMiddleware(app, token) if token else app


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog=SERVER_NAME, description=INSTRUCTIONS.splitlines()[0])
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="streamable-http only")
    parser.add_argument("--port", type=int, default=8000, help="streamable-http only")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help=f"streamable-http only: allow a non-loopback --host (needs {TOKEN_ENV})",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        metavar="HOST[:PORT]",
        help="streamable-http only: extra Host header to accept (repeatable)",
    )
    args = parser.parse_args(argv)
    if args.transport != "streamable-http":
        build_server().run("stdio")
        return
    token = os.environ.get(TOKEN_ENV, "").strip() or None
    remote = not is_loopback_host(args.host)
    if remote and not args.allow_remote:
        parser.error(
            f"--host {args.host} is not a loopback address: pass --allow-remote and set "
            f"{TOKEN_ENV} to serve over the network"
        )
    if remote and token is None:
        parser.error(
            f"--allow-remote on --host {args.host} needs {TOKEN_ENV} set to a bearer token"
        )
    server = build_server()
    hosts = allowed_hosts(
        args.host, [*args.allowed_host, *split_hosts(os.environ.get(ALLOWED_HOSTS_ENV))]
    )
    app = http_app(server, args.host, token if remote else None, hosts)
    uvicorn.run(app, host=args.host, port=args.port, log_level=server.settings.log_level.lower())

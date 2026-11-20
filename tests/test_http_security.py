"""The streamable-HTTP transport: remote opt-in, bearer token and allowed Host headers."""

import logging
from collections.abc import Callable, Iterator

import pytest
from mcp.server.mcpserver import MCPServer
from starlette.testclient import TestClient
from starlette.types import ASGIApp

from wasabi_mcp import server as server_module
from wasabi_mcp.server import allowed_hosts, is_loopback_host, main

TOKEN = "test-http-token-7f3a9c"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WASABI_MCP_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("WASABI_MCP_ALLOWED_HOSTS", raising=False)


@pytest.fixture
def no_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fails the test if the server is built or a listener is started."""

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("server must not start")

    monkeypatch.setattr(server_module, "build_server", fail)
    monkeypatch.setattr(server_module.uvicorn, "run", fail)


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch, server: MCPServer) -> Callable[..., dict[str, object]]:
    """Runs ``main`` with ``argv``, capturing what it would hand to uvicorn."""

    def run(*argv: str) -> dict[str, object]:
        captured: dict[str, object] = {}

        def fake_run(app: ASGIApp, **kwargs: object) -> None:
            captured.update(kwargs, app=app)

        monkeypatch.setattr(server_module, "build_server", lambda: server)
        monkeypatch.setattr(server_module.uvicorn, "run", fake_run)
        main(["--transport", "streamable-http", *argv])
        return captured

    return run


def initialize(client: TestClient, **headers: str) -> int:
    return client.post("/mcp", json=INITIALIZE, headers={**MCP_HEADERS, **headers}).status_code


# Startup checks


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "[::1]", "localhost"])
def test_loopback_hosts(host):
    assert is_loopback_host(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "fe80::1", "mcp.example.com"])
def test_non_loopback_hosts(host):
    assert not is_loopback_host(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::", "mcp.example.com"])
def test_non_loopback_host_refused_without_allow_remote(host, no_start, monkeypatch, capsys):
    monkeypatch.setenv("WASABI_MCP_HTTP_TOKEN", TOKEN)
    with pytest.raises(SystemExit) as exit_info:
        main(["--transport", "streamable-http", "--host", host])
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "--allow-remote" in err and "not a loopback address" in err
    assert TOKEN not in err


@pytest.mark.parametrize("token", [None, "", "   "])
def test_allow_remote_refused_without_token(token, no_start, monkeypatch, capsys):
    if token is not None:
        monkeypatch.setenv("WASABI_MCP_HTTP_TOKEN", token)
    with pytest.raises(SystemExit) as exit_info:
        main(["--transport", "streamable-http", "--host", "0.0.0.0", "--allow-remote"])
    assert exit_info.value.code == 2
    assert "WASABI_MCP_HTTP_TOKEN" in capsys.readouterr().err


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "localhost"])
def test_loopback_host_starts_without_token(host, serve):
    started = serve("--host", host, "--port", "8123")
    assert started["host"] == host and started["port"] == 8123
    assert not isinstance(started["app"], server_module.BearerTokenMiddleware)


def test_remote_host_starts_with_opt_in_and_token(serve, monkeypatch):
    monkeypatch.setenv("WASABI_MCP_HTTP_TOKEN", TOKEN)
    started = serve("--host", "0.0.0.0", "--allow-remote")
    assert started["host"] == "0.0.0.0"
    assert isinstance(started["app"], server_module.BearerTokenMiddleware)


# Bearer token


@pytest.fixture
def remote_client(serve, monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setenv("WASABI_MCP_HTTP_TOKEN", TOKEN)
    started = serve("--host", "0.0.0.0", "--allow-remote", "--allowed-host", "mcp.example.com")
    with TestClient(started["app"], base_url="http://mcp.example.com") as client:
        yield client


@pytest.mark.parametrize("path", ["/mcp", "/unknown"])
@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer wrong-token", f"Basic {TOKEN}", TOKEN, "Bearer", f"Bearer {TOKEN}x"],
)
def test_request_without_valid_token_gets_401(remote_client, path, authorization):
    headers = {**MCP_HEADERS, **({"Authorization": authorization} if authorization else {})}
    response = remote_client.post(path, json=INITIALIZE, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert TOKEN not in response.text


def test_request_with_token_reaches_mcp_app(remote_client):
    assert initialize(remote_client, Authorization=f"Bearer {TOKEN}") == 200
    assert initialize(remote_client, Authorization=f"bearer {TOKEN}") == 200


def test_default_loopback_served_without_token(serve):
    started = serve()
    with TestClient(started["app"], base_url="http://127.0.0.1:8000") as client:
        assert initialize(client) == 200


def test_token_never_logged_or_printed(remote_client, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    initialize(remote_client, Authorization="Bearer wrong")
    initialize(remote_client, Authorization=f"Bearer {TOKEN}")
    output = capsys.readouterr()
    assert TOKEN not in caplog.text
    assert TOKEN not in output.out + output.err


# Allowed Host headers


def test_allowed_hosts_normalised():
    hosts = allowed_hosts("0.0.0.0", ["mcp.example.com", "proxy:8443", "fe80::1", "x:*", " "])
    assert hosts == [
        "0.0.0.0",
        "0.0.0.0:*",
        "127.0.0.1",
        "127.0.0.1:*",
        "localhost",
        "localhost:*",
        "[::1]",
        "[::1]:*",
        "mcp.example.com",
        "mcp.example.com:*",
        "proxy:8443",
        "[fe80::1]",
        "[fe80::1]:*",
        "x:*",
    ]


def test_allowed_hosts_deduplicated():
    assert allowed_hosts("127.0.0.1", ["localhost", "127.0.0.1"]).count("127.0.0.1") == 1


@pytest.mark.parametrize(
    "host", ["evil.example.com", "evil.example.com:8000", "proxy:9999", "testserver"]
)
def test_host_not_allowed_gets_421(serve, monkeypatch, host):
    monkeypatch.setenv("WASABI_MCP_HTTP_TOKEN", TOKEN)
    started = serve("--host", "0.0.0.0", "--allow-remote", "--allowed-host", "proxy:8443")
    with TestClient(started["app"], base_url="http://proxy:8443") as client:
        assert initialize(client, Authorization=f"Bearer {TOKEN}", Host=host) == 421


@pytest.mark.parametrize(
    "host", ["127.0.0.1:8000", "127.0.0.1", "localhost:8000", "[::1]:8000", "localhost"]
)
def test_default_loopback_host_headers_accepted(serve, host):
    started = serve()
    with TestClient(started["app"], base_url="http://127.0.0.1:8000") as client:
        assert initialize(client, Host=host) == 200


def test_loopback_default_rejects_foreign_host(serve):
    started = serve()
    with TestClient(started["app"], base_url="http://127.0.0.1:8000") as client:
        assert initialize(client, Host="attacker.example:8000") == 421


@pytest.mark.parametrize("host", ["10.0.0.5", "10.0.0.5:8000"])
def test_bound_host_accepted(serve, monkeypatch, host):
    monkeypatch.setenv("WASABI_MCP_HTTP_TOKEN", TOKEN)
    started = serve("--host", "10.0.0.5", "--allow-remote")
    with TestClient(started["app"], base_url="http://10.0.0.5:8000") as client:
        assert initialize(client, Authorization=f"Bearer {TOKEN}", Host=host) == 200


@pytest.mark.parametrize(
    "host", ["mcp.example.com", "mcp.example.com:443", "proxy:8443", "other.example"]
)
def test_extra_allowed_hosts_from_flag_and_env(serve, monkeypatch, host):
    monkeypatch.setenv("WASABI_MCP_HTTP_TOKEN", TOKEN)
    monkeypatch.setenv("WASABI_MCP_ALLOWED_HOSTS", " proxy:8443 ,, other.example ")
    started = serve("--host", "0.0.0.0", "--allow-remote", "--allowed-host", "mcp.example.com")
    with TestClient(started["app"], base_url="http://mcp.example.com") as client:
        assert initialize(client, Authorization=f"Bearer {TOKEN}", Host=host) == 200


def test_allowed_origin_accepted_and_foreign_origin_refused(serve):
    started = serve()
    with TestClient(started["app"], base_url="http://127.0.0.1:8000") as client:
        assert initialize(client, Origin="http://localhost:3000") == 200
        assert initialize(client, Origin="https://evil.example") == 403

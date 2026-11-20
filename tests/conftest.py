"""Fixtures shared by every ticket's tests (PRD section 4.3)."""

from __future__ import annotations

import os
import urllib.request
from collections.abc import Iterator

import pytest
from mcp.server.mcpserver import MCPServer
from moto.server import ThreadedMotoServer

from wasabi_mcp.config import Settings
from wasabi_mcp.server import build_server

# Fake credentials. Recognisable so tests can assert they never appear in output.
FAKE_ACCESS_KEY = "AKIAFAKEWASABIKEY000"
FAKE_SECRET_KEY = "fake-secret-key-must-never-leak"
FAKE_WACM_USERNAME = "fake-wacm-user"
FAKE_WACM_API_KEY = "fake-wacm-api-key-must-never-leak"
SECRETS = (FAKE_SECRET_KEY, FAKE_WACM_API_KEY)

# Fake hosts for respx-mocked tests (Wasabi extensions, Stats, WACM).
FAKE = {
    "s3": "https://s3.wasabi.test",
    "iam": "https://iam.wasabi.test",
    "sts": "https://sts.wasabi.test",
    "stats": "https://stats.wasabi.test",
    "wacm": "https://wacm.wasabi.test/api",
}


@pytest.fixture(scope="session")
def _moto_server() -> Iterator[str]:
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


@pytest.fixture
def moto_url(_moto_server: str) -> Iterator[str]:
    """A moto server for S3, IAM and STS, reset after each test that uses it.

    The reset goes through urllib, so respx (which patches httpx) never sees it.
    """
    yield _moto_server
    request = urllib.request.Request(f"{_moto_server}/moto-api/reset", method="POST")
    with urllib.request.urlopen(request, timeout=10):
        pass


def _moto_settings(url: str, *, read_only: bool) -> Settings:
    return Settings(
        access_key_id=FAKE_ACCESS_KEY,
        secret_access_key=FAKE_SECRET_KEY,
        region="us-east-1",
        read_only=read_only,
        s3_endpoint=url,
        iam_endpoint=url,
        sts_endpoint=url,
    )


@pytest.fixture
def settings(moto_url: str) -> Settings:
    """Writes allowed; S3, IAM and STS point at moto."""
    return _moto_settings(moto_url, read_only=False)


@pytest.fixture
def read_only_settings(moto_url: str) -> Settings:
    """Read-only; S3, IAM and STS point at moto."""
    return _moto_settings(moto_url, read_only=True)


@pytest.fixture
def server(settings: Settings) -> MCPServer:
    return build_server(settings)


@pytest.fixture
def read_only_server(read_only_settings: Settings) -> MCPServer:
    return build_server(read_only_settings)


@pytest.fixture
def rest_settings() -> Settings:
    """Writes allowed, WACM configured, every endpoint on a ``FAKE`` host (for respx)."""
    return Settings(
        access_key_id=FAKE_ACCESS_KEY,
        secret_access_key=FAKE_SECRET_KEY,
        region="us-east-1",
        read_only=False,
        wacm_username=FAKE_WACM_USERNAME,
        wacm_api_key=FAKE_WACM_API_KEY,
        s3_endpoint=FAKE["s3"],
        iam_endpoint=FAKE["iam"],
        sts_endpoint=FAKE["sts"],
        stats_endpoint=FAKE["stats"],
        wacm_endpoint=FAKE["wacm"],
    )


@pytest.fixture
def rest_server(rest_settings: Settings) -> MCPServer:
    return build_server(rest_settings)


@pytest.fixture
def live_settings() -> Settings:
    """Real Wasabi settings from the environment. Skips unless ``WASABI_LIVE=1``."""
    if os.environ.get("WASABI_LIVE") != "1":
        pytest.skip("set WASABI_LIVE=1 (and Wasabi keys) to run live tests")
    return Settings.from_env()

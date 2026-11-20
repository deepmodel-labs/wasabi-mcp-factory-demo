"""Test support for the IAM and STS tool groups.

boto3 sends requests with urllib3, so respx (which patches httpx) can't see them. Wasabi
behaviour that moto doesn't model on boto3 calls (``UserName=*``, ``DeleteConflict``) is
stubbed with botocore's ``Stubber`` instead; respx still covers ``rest.iam_query``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from botocore.stub import Stubber
from mcp.server.mcpserver import MCPServer

from tests.conftest import SECRETS
from wasabi_mcp.clients import Clients
from wasabi_mcp.config import Settings
from wasabi_mcp.server import build_server

READ_ONLY_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": ["s3:GetObject", "s3:ListBucket"],
            "Resource": ["arn:aws:s3:::bucket-x", "arn:aws:s3:::bucket-x/*"],
        }
    ],
}


class StubbedIam:
    """A server whose IAM (and STS) clients are botocore ``Stubber``s."""

    def __init__(self, server: MCPServer, iam: Stubber, sts: Stubber) -> None:
        self.server = server
        self.iam = iam
        self.sts = sts


def stubbed(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[StubbedIam]:
    """Build a server on ``settings`` whose IAM and STS calls go to Stubbers."""
    real = Clients(settings)
    iam_client, sts_client = real.iam(), real.sts()
    monkeypatch.setattr(Clients, "iam", lambda self: iam_client)
    monkeypatch.setattr(Clients, "sts", lambda self: sts_client)
    with Stubber(iam_client) as iam, Stubber(sts_client) as sts:
        yield StubbedIam(build_server(settings), iam, sts)
        iam.assert_no_pending_responses()
        sts.assert_no_pending_responses()


def client_error(code: str, message: str, status: int = 409) -> dict[str, Any]:
    """Keyword arguments for ``Stubber.add_client_error``."""
    return {"service_error_code": code, "service_message": message, "http_status_code": status}


def assert_no_secrets(result: Any, *extra: str) -> None:
    """No configured secret (or any ``extra`` value, or a SecretAccessKey) in ``result``."""
    text = json.dumps(result, default=str)
    for secret in (*SECRETS, *extra):
        assert secret not in text
    assert "SecretAccessKey" not in text
    assert "secret_access_key" not in text

"""Cached boto3 clients for S3, IAM and STS."""

from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config

from wasabi_mcp.config import Settings

# IAM and STS are global services signed for us-east-1.
GLOBAL_REGION = "us-east-1"


class Clients:
    """Lazily created boto3 clients, one per service (and per region for S3)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._session = boto3.session.Session(
            aws_access_key_id=settings.access_key_id,
            aws_secret_access_key=settings.secret_access_key,
        )
        self._cache: dict[tuple[str, str], Any] = {}

    def _client(self, service: str, region: str, endpoint: str, config: Config) -> Any:
        key = (service, region)
        if key not in self._cache:
            self._cache[key] = self._session.client(
                service,
                region_name=region,
                endpoint_url=endpoint,
                config=config,
            )
        return self._cache[key]

    def s3(self, region: str | None = None) -> Any:
        """An S3 client using path-style addressing on ``region``'s endpoint."""
        name = region or self.settings.region
        config = Config(
            retries={"mode": "standard"},
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        )
        return self._client("s3", name, self.settings.s3_endpoint_for(name), config)

    def iam(self) -> Any:
        """An IAM client signed for us-east-1."""
        config = Config(retries={"mode": "standard"})
        return self._client("iam", GLOBAL_REGION, self.settings.iam_url, config)

    def sts(self) -> Any:
        """An STS client signed for us-east-1."""
        config = Config(retries={"mode": "standard"})
        return self._client("sts", GLOBAL_REGION, self.settings.sts_url, config)

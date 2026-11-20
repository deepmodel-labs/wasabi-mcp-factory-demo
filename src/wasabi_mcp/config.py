"""Settings read once from the environment (PRD section 7)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_REGION = "us-east-1"
DEFAULT_IAM_URL = "https://iam.wasabisys.com"
DEFAULT_STS_URL = "https://sts.wasabisys.com"
DEFAULT_STATS_URL = "https://stats.wasabisys.com"
DEFAULT_WACM_URL = "https://api.wacm.wasabisys.com/api"

_TRUE = frozenset({"true", "1", "yes"})
_FALSE = frozenset({"false", "0", "no"})


def parse_bool(name: str, raw: str | None, default: bool) -> bool:
    """Parse a boolean environment value: true/false, 1/0 or yes/no, case-insensitive."""
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"{name} must be one of true/false, 1/0 or yes/no, not {raw!r}")


def _optional(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name, "").strip()
    return value or None


def _optional_dir(env: Mapping[str, str], name: str) -> Path | None:
    """An optional directory path: ``~`` expanded and made absolute."""
    value = _optional(env, name)
    return Path(value).expanduser().resolve() if value else None


@dataclass(frozen=True)
class Settings:
    """Server configuration. Credentials are kept out of ``repr``."""

    access_key_id: str | None = field(default=None, repr=False)
    secret_access_key: str | None = field(default=None, repr=False)
    region: str = DEFAULT_REGION
    read_only: bool = True
    wacm_username: str | None = field(default=None, repr=False)
    wacm_api_key: str | None = field(default=None, repr=False)
    s3_endpoint: str | None = None
    iam_endpoint: str | None = None
    sts_endpoint: str | None = None
    stats_endpoint: str | None = None
    wacm_endpoint: str | None = None
    secrets_dir: Path | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build settings from the environment (or the mapping given)."""
        source = os.environ if env is None else env
        return cls(
            access_key_id=_optional(source, "WASABI_ACCESS_KEY_ID"),
            secret_access_key=_optional(source, "WASABI_SECRET_ACCESS_KEY"),
            region=_optional(source, "WASABI_REGION") or DEFAULT_REGION,
            read_only=parse_bool("WASABI_MCP_READ_ONLY", source.get("WASABI_MCP_READ_ONLY"), True),
            wacm_username=_optional(source, "WASABI_WACM_USERNAME"),
            wacm_api_key=_optional(source, "WASABI_WACM_API_KEY"),
            s3_endpoint=_optional(source, "WASABI_S3_ENDPOINT"),
            iam_endpoint=_optional(source, "WASABI_IAM_ENDPOINT"),
            sts_endpoint=_optional(source, "WASABI_STS_ENDPOINT"),
            stats_endpoint=_optional(source, "WASABI_STATS_ENDPOINT"),
            wacm_endpoint=_optional(source, "WASABI_WACM_ENDPOINT"),
            secrets_dir=_optional_dir(source, "WASABI_MCP_SECRETS_DIR"),
        )

    def s3_endpoint_for(self, region: str | None = None) -> str:
        """The S3 endpoint for ``region`` (default: the settings region)."""
        if self.s3_endpoint:
            return self.s3_endpoint.rstrip("/")
        return f"https://s3.{region or self.region}.wasabisys.com"

    @property
    def iam_url(self) -> str:
        return (self.iam_endpoint or DEFAULT_IAM_URL).rstrip("/")

    @property
    def sts_url(self) -> str:
        return (self.sts_endpoint or DEFAULT_STS_URL).rstrip("/")

    @property
    def stats_url(self) -> str:
        return (self.stats_endpoint or DEFAULT_STATS_URL).rstrip("/")

    @property
    def wacm_url(self) -> str:
        return (self.wacm_endpoint or DEFAULT_WACM_URL).rstrip("/")

    @property
    def has_keys(self) -> bool:
        return bool(self.access_key_id and self.secret_access_key)

    @property
    def has_wacm(self) -> bool:
        return bool(self.wacm_username and self.wacm_api_key)

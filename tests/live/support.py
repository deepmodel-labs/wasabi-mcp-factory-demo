"""Helpers shared by the live tests."""

from __future__ import annotations

import urllib.request
from typing import Any


def fetch(url: str) -> bytes:
    """GET ``url`` with no credentials (for presigned URLs)."""
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read()


def read_only_policy(bucket: str) -> dict[str, Any]:
    """A policy document allowing reads of ``bucket`` and its objects."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
            }
        ],
    }

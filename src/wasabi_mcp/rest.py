"""Requests boto3 can't make: Wasabi S3/IAM extensions, the Stats API and WACM Connect.

Every helper raises ``ToolRefused`` before any network call when its credentials are
missing, and with the service's own error code and message on a non-2xx response.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlencode
from xml.etree import ElementTree

import httpx
from botocore.auth import UNSIGNED_PAYLOAD, SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

from wasabi_mcp.clients import GLOBAL_REGION
from wasabi_mcp.config import Settings
from wasabi_mcp.safety import ToolRefused

TIMEOUT = httpx.Timeout(60.0)
IAM_VERSION = "2010-05-08"

Query = Mapping[str, str | int | bool | None]


class _S3SigV4(SigV4Auth):
    """SigV4 for S3: the path is signed as sent, without dot-segment normalisation."""

    def _normalize_url_path(self, path: str) -> str:
        return path


def parse_xml(content: bytes | str) -> ElementTree.Element:
    """Parse an XML document, stripping namespaces so ``find("Code")`` just works."""
    root = ElementTree.fromstring(content)
    for element in root.iter():
        if isinstance(element.tag, str) and element.tag.startswith("{"):
            element.tag = element.tag.split("}", 1)[1]
    return root


def _error_element(root: ElementTree.Element) -> ElementTree.Element | None:
    """The ``<Error>`` of an S3 ``<Error>`` or IAM ``<ErrorResponse><Error>`` body."""
    return root if root.tag == "Error" else root.find(".//Error")


def _error_message(error: ElementTree.Element, default: str) -> str:
    """An ``<Error>``'s ``Message`` (else ``default``), with `` (<Detail>)`` when present."""
    message = error.findtext("Message") or default
    detail = (error.findtext("Detail") or "").strip()
    return f"{message} ({detail})" if detail else message


def _xml_error(response: httpx.Response) -> tuple[str, str]:
    """(code, message) from an S3 ``<Error>`` or IAM ``<ErrorResponse><Error>`` body."""
    try:
        root = parse_xml(response.content)
    except ElementTree.ParseError:
        root = None
    if root is not None:
        error = _error_element(root)
        if error is not None:
            code = error.findtext("Code") or f"HTTP {response.status_code}"
            return code, _error_message(error, response.reason_phrase)
    text = response.text.strip()[:500] or response.reason_phrase
    return f"HTTP {response.status_code}", text


def _require_keys(settings: Settings, service: str) -> Credentials:
    if not settings.has_keys:
        raise ToolRefused(f"{service} needs WASABI_ACCESS_KEY_ID and WASABI_SECRET_ACCESS_KEY")
    return Credentials(settings.access_key_id, settings.secret_access_key)


def _render_query(query: Query | None) -> str:
    """AWS-encode ``query``; a ``None`` value renders as a bare key (``?compose``)."""
    if not query:
        return ""
    parts = []
    for key, value in query.items():
        name = quote(str(key), safe="-_.~")
        if value is None:
            parts.append(name)
        else:
            text = str(value).lower() if isinstance(value, bool) else str(value)
            parts.append(f"{name}={quote(text, safe='-_.~')}")
    return "?" + "&".join(parts)


def _send(method: str, url: str, headers: Mapping[str, str], body: bytes | None) -> httpx.Response:
    with httpx.Client(timeout=TIMEOUT) as client:
        return client.send(client.build_request(method, url, headers=dict(headers), content=body))


def _sign(
    signer: SigV4Auth,
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
) -> dict[str, str]:
    request = AWSRequest(method=method, url=url, headers=dict(headers), data=body or b"")
    signer.add_auth(request)
    return dict(request.headers.items())


def signed_s3(
    settings: Settings,
    method: str,
    bucket: str,
    key: str = "",
    *,
    query: Query | None = None,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    region: str | None = None,
    action: str,
) -> httpx.Response:
    """A path-style, SigV4-signed S3 request with any method (including ``MOVE``).

    ``query`` values of ``None`` are bare subresources: ``{"inventory": None, "id": "x"}``
    sends ``?inventory&id=x``. The URL signed is exactly the URL sent.
    """
    credentials = _require_keys(settings, action)
    name = region or settings.region
    path = "/" + quote(bucket, safe="")
    if key:
        path += "/" + quote(key, safe="/~")
    url = settings.s3_endpoint_for(name) + path + _render_query(query)
    request_headers = {**(headers or {}), "X-Amz-Content-SHA256": UNSIGNED_PAYLOAD}
    signed = _sign(_S3SigV4(credentials, "s3", name), method.upper(), url, request_headers, body)
    response = _send(method.upper(), url, signed, body)
    if not response.is_success:
        code, message = _xml_error(response)
        raise ToolRefused(f"{action} failed: {code}: {message}")
    return response


def iam_query(
    settings: Settings, action_name: str, params: Mapping[str, Any] | None = None
) -> ElementTree.Element:
    """A SigV4-signed (``iam``, us-east-1) Query API form POST; returns the parsed XML.

    Fields are percent-encoded as botocore does (a space is ``%20``, not ``+``). A 2xx
    body holding an ``<Error>`` with a ``Code`` is raised like a non-2xx response. A 2xx
    body that isn't well-formed XML is parsed from its ``<{action_name}Response>`` element
    alone, else refused.
    """
    credentials = _require_keys(settings, action_name)
    fields = {"Action": action_name, "Version": IAM_VERSION}
    fields.update({k: str(v) for k, v in (params or {}).items() if v is not None})
    body = urlencode(fields, quote_via=quote, safe="-_.~").encode()
    url = settings.iam_url + "/"
    headers = {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}
    signed = _sign(SigV4Auth(credentials, "iam", GLOBAL_REGION), "POST", url, headers, body)
    response = _send("POST", url, signed, body)
    if not response.is_success:
        code, message = _xml_error(response)
        raise ToolRefused(f"{action_name} failed: {code}: {message}")
    root = _parse_action_response(response, action_name)
    error = _error_element(root)
    if error is not None and error.findtext("Code"):
        message = _error_message(error, "no message")
        raise ToolRefused(f"{action_name} failed: {error.findtext('Code')}: {message}")
    return root


def _parse_action_response(response: httpx.Response, action_name: str) -> ElementTree.Element:
    """Parse an IAM Query API body; if it isn't well-formed XML, parse just its
    ``<{action_name}Response>`` element (Wasabi's ValidatePolicy appends stray tags)."""
    try:
        return parse_xml(response.content)
    except ElementTree.ParseError:
        pass
    text = response.text
    tag = f"{action_name}Response"
    start = text.find(f"<{tag}")
    end = text.find(f"</{tag}>", start)
    if start != -1 and end != -1:
        try:
            return parse_xml(text[start : end + len(tag) + 3])
        except ElementTree.ParseError:
            pass
    raise ToolRefused(f"{action_name} failed: the response wasn't valid XML")


def _json(response: httpx.Response) -> Any:
    return response.json() if response.content else None


def _json_error(response: httpx.Response) -> str:
    """The ``error`` (or ``message``) field of a JSON error body, else the raw text."""
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = None
    if isinstance(payload, dict):
        for field in ("error", "message", "Message"):
            if payload.get(field):
                return str(payload[field])
    return response.text.strip()[:500] or response.reason_phrase


def stats_get(settings: Settings, path: str, params: Mapping[str, Any] | None = None) -> Any:
    """A Stats API GET with ``Authorization: <access key>:<secret key>``; returns JSON.

    Parameters set to ``None`` are dropped.
    """
    if not settings.has_keys:
        raise ToolRefused("Stats API needs WASABI_ACCESS_KEY_ID and WASABI_SECRET_ACCESS_KEY")
    query = {k: v for k, v in (params or {}).items() if v is not None}
    headers = {
        "Authorization": f"{settings.access_key_id}:{settings.secret_access_key}",
        "Accept": "application/json",
    }
    with httpx.Client(timeout=TIMEOUT) as client:
        response = client.get(settings.stats_url + path, params=query, headers=headers)
    if not response.is_success:
        raise ToolRefused(
            f"Stats API GET {path} failed: HTTP {response.status_code}: {_json_error(response)}"
        )
    return _json(response)


def wacm_request(
    settings: Settings,
    method: str,
    path: str,
    *,
    params: Mapping[str, Any] | None = None,
    body: Any = None,
) -> Any:
    """A WACM Connect request with HTTP Basic auth (WACM username and API key); returns JSON.

    JSON errors ``{error, code}`` are reported as ``HTTP <status>: <error>``.
    """
    if not settings.has_wacm:
        raise ToolRefused(
            "WACM Connect isn't configured: set WASABI_WACM_USERNAME and WASABI_WACM_API_KEY"
        )
    query = {k: v for k, v in (params or {}).items() if v is not None}
    auth = httpx.BasicAuth(settings.wacm_username or "", settings.wacm_api_key or "")
    with httpx.Client(timeout=TIMEOUT, auth=auth) as client:
        response = client.request(
            method.upper(),
            settings.wacm_url + path,
            params=query,
            json=body,
            headers={"Accept": "application/json"},
        )
    if not response.is_success:
        raise ToolRefused(
            f"WACM Connect {method.upper()} {path} failed: "
            f"HTTP {response.status_code}: {_json_error(response)}"
        )
    return _json(response)

"""Helpers shared by the IAM and STS tool groups.

A private module: ``discover_groups`` skips names starting with ``_``, so it registers no
tools. It holds IAM pagination, policy-document handling, ``principal_type`` dispatch,
XML-to-JSON for ``rest.iam_query`` results and error hints.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, Literal
from urllib.parse import unquote
from xml.etree import ElementTree

from botocore.exceptions import BotoCoreError, ClientError

from wasabi_mcp.errors import explain
from wasabi_mcp.safety import ToolRefused
from wasabi_mcp.tools import clean

DEFAULT_MAX_ITEMS = 100
MAX_ITEMS_CAP = 1000

PrincipalType = Literal["user", "group", "role"]

_PRINCIPAL_KEYS: Mapping[str, str] = {
    "user": "UserName",
    "group": "GroupName",
    "role": "RoleName",
}

SECRET_NOTE = "This secret is shown only once and can't be retrieved again. Store it now."

DELETE_CONFLICT = "DeleteConflict"
LIMIT_EXCEEDED = "LimitExceeded"
ACCESS_DENIED = "AccessDenied"
NO_SUCH_ENTITY = "NoSuchEntity"

POLICY_VERSION_LIMIT_HINT = (
    "A managed policy can have at most five versions: delete a non-default version with "
    "iam_policy_version_delete, then try again."
)
ROOT_ASSUME_ROLE_HINT = (
    "Wasabi doesn't allow the root account to assume roles: call this with an IAM user's "
    "credentials that the role's trust policy allows."
)


def delete_conflict_hint(blockers: str) -> str:
    """The hint for a ``DeleteConflict`` on an entity whose dependents are ``blockers``."""
    return f"It still has {blockers}. Remove them first, or call again with cascade=true."


# Errors


def error_code(exc: Exception) -> str | None:
    """The service error code of a ``ClientError``, else ``None``."""
    if isinstance(exc, ClientError):
        return exc.response.get("Error", {}).get("Code")
    return None


def explain_with_hints(
    exc: Exception, action: str, hints: Mapping[str, str] | None = None
) -> ToolRefused:
    """``explain(exc, action)`` with the hint for the error's code appended, if any."""
    refused = explain(exc, action)
    hint = (hints or {}).get(error_code(exc) or "")
    return ToolRefused(f"{refused} {hint}") if hint else refused


def invoke(
    action: str,
    method: Callable[..., Any],
    /,
    *,
    hints: Mapping[str, str] | None = None,
    **params: Any,
) -> dict[str, Any]:
    """Call a boto3 ``method`` with ``params`` (``None`` values dropped).

    Botocore errors become ``ToolRefused`` naming ``action``, with any matching hint.
    """
    try:
        return method(**drop_none(params))
    except (ClientError, BotoCoreError) as exc:
        raise explain_with_hints(exc, action, hints) from exc


def drop_none(params: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in params.items() if value is not None}


# Pagination


def page_params(max_items: int, continuation_token: str | None) -> dict[str, Any]:
    """IAM ``MaxItems``/``Marker`` for a list call; ``max_items`` is clamped to 1..1,000."""
    params: dict[str, Any] = {"MaxItems": max(1, min(max_items, MAX_ITEMS_CAP))}
    if continuation_token:
        params["Marker"] = continuation_token
    return params


def page_result(
    response: Mapping[str, Any],
    key: str,
    transform: Callable[[Any], Any] = clean,
) -> dict[str, Any]:
    """``{"items", "next_token", "truncated"}`` from an IAM list response."""
    truncated = bool(response.get("IsTruncated"))
    return {
        "items": [transform(item) for item in response.get(key, [])],
        "next_token": response.get("Marker") if truncated else None,
        "truncated": truncated,
    }


def collect(iam: Any, operation: str, key: str, action: str, **params: Any) -> list[Any]:
    """Every item under ``key`` across all pages of an IAM list ``operation``."""
    try:
        pages = iam.get_paginator(operation).paginate(**drop_none(params))
        return [item for page in pages for item in page.get(key, [])]
    except (ClientError, BotoCoreError) as exc:
        raise explain(exc, action) from exc


# Policy documents


def policy_json(document: dict[str, Any] | str, field: str = "policy_document") -> str:
    """A policy document given as a dict or JSON string, as the compact JSON string IAM
    expects, encoded exactly once (a JSON string is parsed and re-dumped, not quoted again).

    Invalid JSON, or JSON that isn't an object, is refused before any network call.
    """
    if isinstance(document, str):
        try:
            parsed = json.loads(document)
        except json.JSONDecodeError as exc:
            raise ToolRefused(f"{field} isn't valid JSON: {exc.msg} at position {exc.pos}") from exc
    else:
        parsed = document
    if not isinstance(parsed, dict):
        raise ToolRefused(f"{field} must be a JSON object")
    return json.dumps(parsed, separators=(",", ":"))


def decode_policy(document: Any) -> Any:
    """A returned policy document as a JSON object.

    boto3 already decodes most documents; strings are URL-decoded and parsed. A string
    that still isn't JSON is returned as it is.
    """
    if not isinstance(document, str):
        return document
    for candidate in (document, unquote(document)):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return document


# Principals


def principal_key(principal_type: PrincipalType) -> str:
    """The IAM parameter naming a principal: ``UserName``, ``GroupName`` or ``RoleName``."""
    try:
        return _PRINCIPAL_KEYS[principal_type]
    except KeyError as exc:
        raise ToolRefused(
            f"principal_type must be user, group or role, not {principal_type!r}"
        ) from exc


def principal_method(iam: Any, principal_type: PrincipalType, template: str) -> Callable[..., Any]:
    """The boto3 method for ``principal_type``: ``template`` is e.g. ``"attach_{}_policy"``."""
    principal_key(principal_type)
    return getattr(iam, template.format(principal_type))


def principal_action(principal_type: PrincipalType, template: str) -> str:
    """The IAM action name for ``principal_type``: ``template`` is e.g. ``"Attach{}Policy"``."""
    principal_key(principal_type)
    return template.format(principal_type.capitalize())


# XML from rest.iam_query


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if tag.startswith("{") else tag


def xml_to_json(element: ElementTree.Element) -> Any:
    """An IAM Query API XML element as JSON.

    Namespaces are stripped, leaves become their text, ``member`` children become lists
    and repeated tags become lists.
    """
    children = list(element)
    if not children:
        return (element.text or "").strip() or None
    if all(_local(child.tag) == "member" for child in children):
        return [xml_to_json(child) for child in children]
    result: dict[str, Any] = {}
    for child in children:
        name = _local(child.tag)
        value = xml_to_json(child)
        if name in result:
            existing = result[name]
            result[name] = [*existing, value] if isinstance(existing, list) else [existing, value]
        else:
            result[name] = value
    return result


def query_result(root: ElementTree.Element, action_name: str) -> Any:
    """The ``<ActionResult>`` of an IAM Query API response as JSON (``{}`` when empty)."""
    result = next((el for el in root.iter() if _local(el.tag) == f"{action_name}Result"), None)
    if result is None:
        return {}
    converted = xml_to_json(result)
    return converted if converted is not None else {}

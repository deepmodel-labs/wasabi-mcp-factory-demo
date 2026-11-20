import json
from urllib.parse import quote

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from wasabi_mcp.rest import parse_xml
from wasabi_mcp.safety import ToolRefused
from wasabi_mcp.tools import _iam
from wasabi_mcp.tools._iam import (
    decode_policy,
    error_code,
    explain_with_hints,
    invoke,
    page_params,
    page_result,
    policy_json,
    principal_action,
    principal_key,
    principal_method,
    query_result,
    xml_to_json,
)


def _client_error(code: str, message: str = "boom") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "DeleteUser")


def test_page_params_defaults_and_clamps():
    assert page_params(100, None) == {"MaxItems": 100}
    assert page_params(5000, "m1") == {"MaxItems": 1000, "Marker": "m1"}
    assert page_params(0, "") == {"MaxItems": 1}


def test_page_result_maps_marker_only_when_truncated():
    truncated = page_result(
        {"Users": [{"UserName": "a"}], "IsTruncated": True, "Marker": "m"}, "Users"
    )
    assert truncated == {"items": [{"UserName": "a"}], "next_token": "m", "truncated": True}
    done = page_result({"Users": [], "IsTruncated": False, "Marker": "stale"}, "Users")
    assert done == {"items": [], "next_token": None, "truncated": False}


def test_page_result_applies_transform():
    result = page_result({"Names": ["a", "b"]}, "Names", str.upper)
    assert result["items"] == ["A", "B"]


def test_policy_json_accepts_dict_and_string():
    document = {"Version": "2012-10-17", "Statement": []}
    assert json.loads(policy_json(document)) == document
    assert json.loads(policy_json(json.dumps(document))) == document


def test_policy_json_refuses_bad_json_and_non_objects():
    with pytest.raises(ToolRefused, match="policy_document isn't valid JSON"):
        policy_json("{not json")
    with pytest.raises(ToolRefused, match="trust must be a JSON object"):
        policy_json("[1, 2]", "trust")


def test_decode_policy_handles_dicts_json_and_url_encoded_strings():
    document = {"Version": "2012-10-17"}
    assert decode_policy(document) is document
    assert decode_policy(json.dumps(document)) == document
    assert decode_policy(quote(json.dumps(document))) == document
    assert decode_policy("not json") == "not json"


def test_principal_dispatch():
    class Iam:
        def attach_group_policy(self):
            return "group"

    assert principal_key("user") == "UserName"
    assert principal_key("role") == "RoleName"
    assert principal_method(Iam(), "group", "attach_{}_policy")() == "group"
    assert principal_action("role", "Attach{}Policy") == "AttachRolePolicy"
    with pytest.raises(ToolRefused, match="principal_type must be user, group or role"):
        principal_key("bucket")  # type: ignore[arg-type]


def test_xml_to_json_strips_namespaces_and_flattens_members():
    root = parse_xml(
        b'<R xmlns="https://iam.amazonaws.com/doc/2010-05-08/">'
        b"<Name>a</Name><Empty/><Items><member>1</member><member>2</member></Items>"
        b"<Items2><member><K>v</K></member></Items2><Rep>x</Rep><Rep>y</Rep></R>"
    )
    assert xml_to_json(root) == {
        "Name": "a",
        "Empty": None,
        "Items": ["1", "2"],
        "Items2": [{"K": "v"}],
        "Rep": ["x", "y"],
    }


def test_query_result_finds_the_action_result():
    root = parse_xml(
        b"<GetUserMFAResponse><GetUserMFAResult><A>1</A></GetUserMFAResult>"
        b"<ResponseMetadata><RequestId>r</RequestId></ResponseMetadata></GetUserMFAResponse>"
    )
    assert query_result(root, "GetUserMFA") == {"A": "1"}
    assert query_result(root, "Other") == {}


def test_error_code_and_hints():
    exc = _client_error(_iam.DELETE_CONFLICT, "has keys")
    assert error_code(exc) == "DeleteConflict"
    assert error_code(ValueError("x")) is None
    hinted = explain_with_hints(exc, "DeleteUser", {"DeleteConflict": "Use cascade."})
    assert str(hinted) == "DeleteUser failed: DeleteConflict: has keys Use cascade."
    plain = explain_with_hints(exc, "DeleteUser", {"Other": "nope"})
    assert str(plain) == "DeleteUser failed: DeleteConflict: has keys"


def test_invoke_drops_none_and_explains_errors():
    seen = {}

    def method(**params):
        seen.update(params)
        return {"ok": True}

    assert invoke("X", method, A=1, B=None) == {"ok": True}
    assert seen == {"A": 1}

    def failing(**params):
        raise EndpointConnectionError(endpoint_url="https://iam.wasabi.test")

    with pytest.raises(ToolRefused, match="X failed: EndpointConnectionError"):
        invoke("X", failing)


def test_delete_conflict_hint_mentions_cascade():
    assert "cascade=true" in _iam.delete_conflict_hint("members")

import base64
import socket
from urllib.parse import parse_qs

import httpcore
import httpx
import pytest
import respx

from tests.conftest import FAKE, FAKE_ACCESS_KEY, FAKE_SECRET_KEY, FAKE_WACM_API_KEY, SECRETS
from wasabi_mcp import rest
from wasabi_mcp.config import Settings
from wasabi_mcp.safety import ToolRefused

NO_CREDENTIALS = Settings(
    s3_endpoint=FAKE["s3"],
    iam_endpoint=FAKE["iam"],
    stats_endpoint=FAKE["stats"],
    wacm_endpoint=FAKE["wacm"],
)

S3_ERROR = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b"<Error><Code>BucketNotEmpty</Code><Message>The bucket is not empty</Message></Error>"
)
IAM_ERROR = (
    b'<ErrorResponse xmlns="https://iam.amazonaws.com/doc/2010-05-08/">'
    b"<Error><Type>Sender</Type><Code>NoSuchEntity</Code>"
    b"<Message>The user with name bob cannot be found.</Message></Error>"
    b"</ErrorResponse>"
)


def test_parse_xml_strips_namespaces():
    root = rest.parse_xml(IAM_ERROR)
    assert root.tag == "ErrorResponse"
    assert root.findtext("Error/Code") == "NoSuchEntity"


@respx.mock
def test_signed_s3_signs_move_with_sigv4_and_unsigned_payload(rest_settings):
    route = respx.route(method="MOVE", host="s3.wasabi.test").mock(return_value=httpx.Response(200))
    rest.signed_s3(
        rest_settings,
        "MOVE",
        "bucket",
        "dir/a b.txt",
        headers={"Destination": "/bucket/dir/c.txt"},
        action="MoveObject",
    )
    request = route.calls.last.request
    assert str(request.url) == f"{FAKE['s3']}/bucket/dir/a%20b.txt"
    assert request.headers["x-amz-content-sha256"] == "UNSIGNED-PAYLOAD"
    authorization = request.headers["authorization"]
    assert authorization.startswith(f"AWS4-HMAC-SHA256 Credential={FAKE_ACCESS_KEY}/")
    assert "/us-east-1/s3/aws4_request" in authorization
    assert "destination;host;x-amz-content-sha256;x-amz-date" in authorization
    assert FAKE_SECRET_KEY not in authorization


@respx.mock
def test_signed_s3_sends_bare_subresources_unchanged(rest_settings):
    route = respx.route(host="s3.wasabi.test").mock(return_value=httpx.Response(200))
    rest.signed_s3(
        rest_settings,
        "GET",
        "bucket",
        query={"inventory": None, "id": "weekly report"},
        region="eu-central-1",
        action="GetBucketInventoryConfiguration",
    )
    request = route.calls.last.request
    assert str(request.url) == f"{FAKE['s3']}/bucket?inventory&id=weekly%20report"
    assert "/eu-central-1/s3/aws4_request" in request.headers["authorization"]


@respx.mock
def test_signed_s3_renders_bools_and_sends_the_body(rest_settings):
    route = respx.route(host="s3.wasabi.test").mock(return_value=httpx.Response(204))
    rest.signed_s3(
        rest_settings,
        "PUT",
        "bucket",
        "key",
        query={"append": None, "force_delete": True},
        body=b"more data",
        action="AppendObject",
    )
    request = route.calls.last.request
    assert request.url.query == b"append&force_delete=true"
    assert request.content == b"more data"


@respx.mock
def test_signed_s3_raises_the_s3_error(rest_settings):
    respx.route(host="s3.wasabi.test").mock(return_value=httpx.Response(409, content=S3_ERROR))
    with pytest.raises(ToolRefused) as exc:
        rest.signed_s3(
            rest_settings, "DELETE", "bucket", query={"force_delete": True}, action="DeleteBucket"
        )
    assert str(exc.value) == "DeleteBucket failed: BucketNotEmpty: The bucket is not empty"


@respx.mock
def test_signed_s3_reports_status_when_the_error_has_no_body(rest_settings):
    respx.route(host="s3.wasabi.test").mock(return_value=httpx.Response(403))
    with pytest.raises(ToolRefused, match="HeadObject failed: HTTP 403: Forbidden"):
        rest.signed_s3(rest_settings, "HEAD", "bucket", "key", action="HeadObject")


@respx.mock
def test_iam_query_posts_a_signed_form(rest_settings):
    route = respx.post(f"{FAKE['iam']}/").mock(
        return_value=httpx.Response(
            200,
            content=b'<GetUserMFAResponse xmlns="https://iam.amazonaws.com/doc/2010-05-08/">'
            b"<GetUserMFAResult><Enabled>false</Enabled></GetUserMFAResult>"
            b"</GetUserMFAResponse>",
        )
    )
    root = rest.iam_query(rest_settings, "GetUserMFA", {"UserName": "bob", "Marker": None})
    assert root.findtext("GetUserMFAResult/Enabled") == "false"
    request = route.calls.last.request
    assert parse_qs(request.content.decode()) == {
        "Action": ["GetUserMFA"],
        "Version": ["2010-05-08"],
        "UserName": ["bob"],
    }
    assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert "/us-east-1/iam/aws4_request" in request.headers["authorization"]


@respx.mock
def test_iam_query_raises_the_iam_error(rest_settings):
    respx.post(f"{FAKE['iam']}/").mock(return_value=httpx.Response(404, content=IAM_ERROR))
    with pytest.raises(ToolRefused) as exc:
        rest.iam_query(rest_settings, "GetUserMFA", {"UserName": "bob"})
    assert str(exc.value) == (
        "GetUserMFA failed: NoSuchEntity: The user with name bob cannot be found."
    )


@respx.mock
def test_iam_query_raises_an_error_body_sent_with_200(rest_settings):
    respx.post(f"{FAKE['iam']}/").mock(return_value=httpx.Response(200, content=IAM_ERROR))
    with pytest.raises(ToolRefused) as exc:
        rest.iam_query(rest_settings, "GetUserMFA", {"UserName": "bob"})
    assert str(exc.value) == (
        "GetUserMFA failed: NoSuchEntity: The user with name bob cannot be found."
    )


@respx.mock
def test_iam_query_parses_the_action_response_out_of_a_malformed_body(rest_settings):
    respx.post(f"{FAKE['iam']}/").mock(
        return_value=httpx.Response(
            200,
            content=b'<ValidatePolicyResponse xmlns="https://iam.amazonaws.com/doc/2010-05-08/">'
            b"<ValidatePolicyResult><Evaluation>Allow</Evaluation></ValidatePolicyResult>"
            b"</ValidatePolicyResponse>"
            b"<ResponseMetadata><RequestId>r1</RequestId></ResponseMetadata>"
            b"</CreatePolicyVersionResponse>",
        )
    )
    root = rest.iam_query(rest_settings, "ValidatePolicy", {})
    assert root.tag == "ValidatePolicyResponse"
    assert root.findtext("ValidatePolicyResult/Evaluation") == "Allow"


@respx.mock
@pytest.mark.parametrize("body", [b"", b"not xml", b"<OtherResponse></Mismatch>"])
def test_iam_query_refuses_a_body_that_isnt_xml(rest_settings, body):
    respx.post(f"{FAKE['iam']}/").mock(return_value=httpx.Response(200, content=body))
    with pytest.raises(ToolRefused) as exc:
        rest.iam_query(rest_settings, "ValidatePolicy", {})
    assert str(exc.value) == "ValidatePolicy failed: the response wasn't valid XML"


@respx.mock
@pytest.mark.parametrize("status", [400, 200])
def test_iam_query_appends_the_error_detail(rest_settings, status):
    body = (
        b"<ErrorResponse><Error><Code>MalformedPolicyDocument</Code>"
        b"<Message>Syntax errors in policy.</Message><Detail>bad token</Detail></Error>"
        b"</ErrorResponse>"
    )
    respx.post(f"{FAKE['iam']}/").mock(return_value=httpx.Response(status, content=body))
    with pytest.raises(ToolRefused) as exc:
        rest.iam_query(rest_settings, "ValidatePolicy", {})
    assert str(exc.value) == (
        "ValidatePolicy failed: MalformedPolicyDocument: Syntax errors in policy. (bad token)"
    )


@respx.mock
def test_signed_s3_appends_the_error_detail(rest_settings):
    body = b"<Error><Code>InvalidArgument</Code><Message>Bad</Message><Detail>why</Detail></Error>"
    respx.route(host="s3.wasabi.test").mock(return_value=httpx.Response(400, content=body))
    with pytest.raises(ToolRefused) as exc:
        rest.signed_s3(rest_settings, "GET", "bucket", action="GetThing")
    assert str(exc.value) == "GetThing failed: InvalidArgument: Bad (why)"


@respx.mock
def test_iam_query_percent_encodes_fields_like_botocore(rest_settings):
    route = respx.post(f"{FAKE['iam']}/").mock(
        return_value=httpx.Response(200, content=b"<GetUserMFAResponse/>")
    )
    rest.iam_query(rest_settings, "GetUserMFA", {"UserName": "a b/c"})
    assert "UserName=a%20b%2Fc" in route.calls.last.request.content.decode()


@respx.mock
def test_stats_get_uses_key_pair_auth_and_drops_none(rest_settings):
    route = respx.get(f"{FAKE['stats']}/v1/standalone/utilizations").mock(
        return_value=httpx.Response(200, json={"Records": []})
    )
    result = rest.stats_get(
        rest_settings, "/v1/standalone/utilizations", {"from": "2026-01-01", "to": None}
    )
    assert result == {"Records": []}
    request = route.calls.last.request
    assert request.headers["authorization"] == f"{FAKE_ACCESS_KEY}:{FAKE_SECRET_KEY}"
    assert request.url.query == b"from=2026-01-01"


@respx.mock
def test_stats_get_raises_on_error(rest_settings):
    respx.get(host="stats.wasabi.test").mock(
        return_value=httpx.Response(401, json={"message": "Invalid credentials"})
    )
    with pytest.raises(ToolRefused, match="HTTP 401: Invalid credentials"):
        rest.stats_get(rest_settings, "/v1/standalone/utilizations", {})


@respx.mock
def test_wacm_request_uses_basic_auth(rest_settings):
    route = respx.post(f"{FAKE['wacm']}/v1/sub-accounts").mock(
        return_value=httpx.Response(201, json={"id": 7})
    )
    result = rest.wacm_request(
        rest_settings, "post", "/v1/sub-accounts", params={"x": None}, body={"name": "a"}
    )
    assert result == {"id": 7}
    request = route.calls.last.request
    expected = base64.b64encode(f"fake-wacm-user:{FAKE_WACM_API_KEY}".encode()).decode()
    assert request.headers["authorization"] == f"Basic {expected}"
    assert request.url.query == b""
    assert request.content == b'{"name":"a"}'


@respx.mock
def test_wacm_request_reports_json_errors(rest_settings):
    respx.get(f"{FAKE['wacm']}/v1/accounts/9").mock(
        return_value=httpx.Response(404, json={"error": "Account not found", "code": 404})
    )
    with pytest.raises(ToolRefused, match="HTTP 404: Account not found"):
        rest.wacm_request(rest_settings, "GET", "/v1/accounts/9")


@pytest.mark.parametrize(
    ("helper", "message"),
    [
        (
            lambda s: rest.signed_s3(s, "GET", "bucket", action="GetBucketCompliance"),
            "GetBucketCompliance needs WASABI_ACCESS_KEY_ID and WASABI_SECRET_ACCESS_KEY",
        ),
        (
            lambda s: rest.iam_query(s, "ValidatePolicy", {}),
            "ValidatePolicy needs WASABI_ACCESS_KEY_ID and WASABI_SECRET_ACCESS_KEY",
        ),
        (
            lambda s: rest.stats_get(s, "/v1/standalone/utilizations", {}),
            "Stats API needs WASABI_ACCESS_KEY_ID and WASABI_SECRET_ACCESS_KEY",
        ),
        (
            lambda s: rest.wacm_request(s, "GET", "/v1/accounts"),
            "WACM Connect isn't configured: set WASABI_WACM_USERNAME and WASABI_WACM_API_KEY",
        ),
    ],
)
def test_missing_credentials_refuse_before_any_request(helper, message):
    with respx.mock(assert_all_called=False) as mock:
        route = mock.route().mock(return_value=httpx.Response(200))
        with pytest.raises(ToolRefused) as exc:
            helper(NO_CREDENTIALS)
        assert str(exc.value) == message
        assert not route.called


# --- transport errors -----------------------------------------------------------------

STATS_PATH = "/v1/standalone/utilizations"
TRANSPORT_CALLS = [
    pytest.param(
        lambda s: rest.signed_s3(s, "PUT", "bucket", "a.txt", body=b"x", action="AppendObject"),
        "AppendObject",
        "s3.wasabi.test",
        id="signed_s3",
    ),
    pytest.param(
        lambda s: rest.iam_query(s, "ValidatePolicy", {}),
        "ValidatePolicy",
        "iam.wasabi.test",
        id="iam_query",
    ),
    pytest.param(
        lambda s: rest.stats_get(s, STATS_PATH, {"from": "2026-01-01"}),
        f"Stats API GET {STATS_PATH}",
        "stats.wasabi.test",
        id="stats_get",
    ),
    pytest.param(
        lambda s: rest.wacm_request(s, "delete", "/v1/sub-accounts/7"),
        "WACM Connect DELETE /v1/sub-accounts/7",
        "wacm.wasabi.test",
        id="wacm_request",
    ),
]


def _raise_through_helper(helper, settings, error: Exception) -> ToolRefused:
    with respx.mock() as mock:
        mock.route().mock(side_effect=error)
        with pytest.raises(ToolRefused) as exc:
            helper(settings)
    assert exc.value.__cause__ is error
    return exc.value


@pytest.mark.parametrize(("helper", "operation", "host"), TRANSPORT_CALLS)
def test_connection_refused_names_operation_host_and_reason(rest_settings, helper, operation, host):
    error = httpx.ConnectError("[Errno 111] Connection refused")
    refused = _raise_through_helper(helper, rest_settings, error)
    assert str(refused) == f"{operation} failed: couldn't connect to {host} (connection refused)"
    assert refused.code == "NetworkError"


@pytest.mark.parametrize(("helper", "operation", "host"), TRANSPORT_CALLS)
@pytest.mark.parametrize(
    "text",
    [
        "[Errno -2] Name or service not known",
        "[Errno 8] nodename nor servname provided, or not known",
        "[Errno -3] Temporary failure in name resolution",
    ],
)
def test_dns_failure_names_the_unresolved_host(rest_settings, helper, operation, host, text):
    refused = _raise_through_helper(helper, rest_settings, httpx.ConnectError(text))
    assert str(refused) == f"{operation} failed: couldn't resolve host {host}"
    assert refused.code == "DNSError"


def test_dns_failure_is_found_in_the_exception_chain():
    # httpx raises ConnectError from httpcore's ConnectError, raised from the gaierror.
    # respx replaces a side effect's __cause__, so this chain is built by hand.
    try:
        try:
            try:
                raise socket.gaierror(socket.EAI_NONAME, "lookup failed")
            except socket.gaierror as gai:
                raise httpcore.ConnectError("connect failed") from gai
        except httpcore.ConnectError as core:
            raise httpx.ConnectError("connect failed") from core
    except httpx.ConnectError as error:
        refused = rest._transport_error("GetUser", f"{FAKE['iam']}/", error)
    assert str(refused) == "GetUser failed: couldn't resolve host iam.wasabi.test"
    assert refused.code == "DNSError"


@pytest.mark.parametrize(("helper", "operation", "host"), TRANSPORT_CALLS)
@pytest.mark.parametrize(
    ("error", "phase"),
    [
        (httpx.ConnectTimeout("timed out"), "connecting to"),
        (httpx.ReadTimeout("timed out"), "reading from"),
    ],
)
def test_timeout_names_the_phase_and_host(rest_settings, helper, operation, host, error, phase):
    refused = _raise_through_helper(helper, rest_settings, error)
    assert str(refused) == f"{operation} failed: timed out {phase} {host}"
    assert refused.code == "Timeout"


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (httpx.ConnectError("[Errno 104] Connection reset by peer"), "(connection reset)"),
        (httpx.ConnectError("[Errno 113] No route to host"), "couldn't connect to s3.wasabi.test"),
        (httpx.RemoteProtocolError("Server disconnected"), "was reset or closed"),
        (httpx.ReadError("[Errno 104] Connection reset by peer"), "was reset or closed"),
        (httpx.UnsupportedProtocol("Request URL has an unsupported protocol"), "network error"),
    ],
)
def test_other_transport_errors_are_classified(rest_settings, error, reason):
    helper = TRANSPORT_CALLS[0].values[0]
    refused = _raise_through_helper(helper, rest_settings, error)
    assert str(refused).startswith("AppendObject failed: ")
    assert reason in str(refused)
    assert "s3.wasabi.test" in str(refused)


def test_dns_timeout_and_refused_messages_are_distinct(rest_settings):
    helper = TRANSPORT_CALLS[2].values[0]
    messages = {
        str(_raise_through_helper(helper, rest_settings, error))
        for error in (
            httpx.ConnectError("[Errno -2] Name or service not known"),
            httpx.ConnectTimeout("timed out"),
            httpx.ConnectError("[Errno 111] Connection refused"),
        )
    }
    assert len(messages) == 3


@pytest.mark.parametrize(("helper", "operation", "host"), TRANSPORT_CALLS)
def test_transport_errors_never_echo_the_request(rest_settings, helper, operation, host):
    leaky = (
        f"[Errno 111] Connection refused: https://{host}/bucket?X-Amz-Credential="
        f"{FAKE_ACCESS_KEY}&X-Amz-Signature=deadbeef Authorization: {FAKE_ACCESS_KEY}:"
        f"{FAKE_SECRET_KEY} Basic {FAKE_WACM_API_KEY}"
    )
    message = str(_raise_through_helper(helper, rest_settings, httpx.ConnectError(leaky)))
    assert message.startswith(f"{operation} failed: couldn't connect to {host}")
    for secret in (*SECRETS, FAKE_ACCESS_KEY, "Signature", "Authorization", "deadbeef"):
        assert secret not in message

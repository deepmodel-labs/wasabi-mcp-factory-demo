from botocore.exceptions import ClientError, EndpointConnectionError

from tests.conftest import FAKE_ACCESS_KEY, FAKE_SECRET_KEY, FAKE_WACM_API_KEY
from wasabi_mcp.config import Settings
from wasabi_mcp.errors import REDACTED, explain, redact
from wasabi_mcp.safety import ToolRefused


def test_client_error_uses_code_and_message():
    exc = ClientError(
        {"Error": {"Code": "NoSuchBucketPolicy", "Message": "The bucket policy does not exist"}},
        "GetBucketPolicy",
    )
    refused = explain(exc, "GetBucketPolicy")
    assert isinstance(refused, ToolRefused)
    assert str(refused) == (
        "GetBucketPolicy failed: NoSuchBucketPolicy: The bucket policy does not exist"
    )


def test_client_error_without_details_still_explains():
    refused = explain(ClientError({"Error": {}}, "HeadBucket"), "HeadBucket")
    assert str(refused).startswith("HeadBucket failed: Unknown: ")


def test_botocore_error_names_the_failure():
    exc = EndpointConnectionError(endpoint_url="https://s3.us-east-1.wasabisys.com")
    refused = explain(exc, "ListBuckets")
    assert str(refused).startswith("ListBuckets failed: EndpointConnectionError: ")
    assert "s3.us-east-1.wasabisys.com" in str(refused)


SETTINGS = Settings(
    access_key_id=FAKE_ACCESS_KEY,
    secret_access_key=FAKE_SECRET_KEY,
    wacm_api_key=FAKE_WACM_API_KEY,
)


def test_redact_removes_configured_credentials():
    text = f"key {FAKE_ACCESS_KEY} secret {FAKE_SECRET_KEY} wacm {FAKE_WACM_API_KEY}"
    assert redact(text, SETTINGS) == f"key {REDACTED} secret {REDACTED} wacm {REDACTED}"


def test_redact_masks_url_query_tokens():
    url = (
        "https://s3.wasabi.test/b/k?X-Amz-Credential=AKIAOTHER%2F20260101&"
        "X-Amz-Security-Token=tok123&X-Amz-Signature=deadbeef&versionId=v1"
    )
    redacted = redact(url, SETTINGS)
    for value in ("AKIAOTHER", "tok123", "deadbeef"):
        assert value not in redacted
    assert redacted.endswith("&versionId=v1")


def test_redact_masks_authorization_header():
    header = (
        "Authorization: AWS4-HMAC-SHA256 Credential=AKIAOTHER/20260101/us-east-1/s3, "
        "SignedHeaders=host, Signature=deadbeef\nnext line"
    )
    assert redact(header, SETTINGS) == f"Authorization: {REDACTED}\nnext line"


def test_redact_masks_dict_repr_and_json():
    text = (
        "{'AccessKeyId': 'AKIAOTHER', 'SecretAccessKey': 'shh', 'SessionToken': 'tok'} "
        '{"secret_access_key": "shh2", "Authorization": "Basic abc"}'
    )
    redacted = redact(text, SETTINGS)
    for value in ("shh", "shh2", "tok'", "Basic abc"):
        assert value not in redacted
    assert "'AccessKeyId': 'AKIAOTHER'" in redacted

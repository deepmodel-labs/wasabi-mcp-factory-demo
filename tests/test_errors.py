from botocore.exceptions import ClientError, EndpointConnectionError

from wasabi_mcp.errors import explain
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

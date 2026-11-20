from urllib.parse import urlsplit
from xml.etree import ElementTree

import boto3
import httpx
import pytest
import respx

from tests.conftest import FAKE, FAKE_ACCESS_KEY, FAKE_SECRET_KEY, SECRETS
from tests.helpers import ToolError, call
from wasabi_mcp.config import Settings
from wasabi_mcp.server import build_server
from wasabi_mcp.tools import DESTRUCTIVE, WRITE

S3 = FAKE["s3"]

MOVE_RESULT = b"""<?xml version="1.0" encoding="UTF-8"?>
<MoveObjectResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <SourcePrefix>logs/</SourcePrefix>
  <DestinationPrefix>archive/</DestinationPrefix>
  <MoveObject><SourceKey>logs/a.txt</SourceKey><DestinationKey>archive/a.txt</DestinationKey>
    <VersionId>v1</VersionId></MoveObject>
  <MoveObject><SourceKey>logs/b.txt</SourceKey><DestinationKey>archive/b.txt</DestinationKey>
    <Error><Code>ObjectExists</Code><Message>The destination exists</Message></Error>
  </MoveObject>
  <MoveCount>1</MoveCount>
</MoveObjectResult>"""


def _error(code: str, message: str) -> httpx.Response:
    body = f"<Error><Code>{code}</Code><Message>{message}</Message></Error>"
    return httpx.Response(400, content=body.encode(), headers={"Content-Type": "application/xml"})


def _assert_signed(request: httpx.Request, region: str = "eu-central-1") -> None:
    authorization = request.headers["authorization"]
    assert authorization.startswith(f"AWS4-HMAC-SHA256 Credential={FAKE_ACCESS_KEY}/")
    assert f"/{region}/s3/aws4_request" in authorization
    assert request.headers["x-amz-content-sha256"] == "UNSIGNED-PAYLOAD"
    assert FAKE_SECRET_KEY not in authorization


# Annotations


async def test_tools_have_catalogue_kinds(rest_server):
    tools = {tool.name: tool for tool in await rest_server.list_tools()}
    assert tools["object_rename"].annotations == DESTRUCTIVE
    assert tools["object_rename_prefix"].annotations == DESTRUCTIVE
    assert tools["object_compose"].annotations == WRITE
    assert tools["object_append"].annotations == WRITE
    assert "hash of the part MD5s" in tools["object_compose"].description
    assert "1,023" in tools["object_append"].description


# object_rename


@respx.mock
async def test_object_rename_sends_move_with_destination_and_overwrite(rest_server):
    route = respx.route(method="MOVE", host="s3.wasabi.test").mock(return_value=httpx.Response(200))
    result = await call(
        rest_server,
        "object_rename",
        bucket="photos",
        key="2024/a b.jpg",
        destination="2025/a b.jpg",
        region="eu-central-1",
        confirm=True,
    )
    request = route.calls.last.request
    assert request.method == "MOVE"
    assert str(request.url) == f"{S3}/photos/2024/a%20b.jpg"
    assert request.headers["destination"] == "2025/a b.jpg"
    assert request.headers["overwrite"] == "false"
    assert "x-wasabi-prefix" not in request.headers
    _assert_signed(request)
    assert "destination;host;overwrite;x-amz-content-sha256" in request.headers["authorization"]
    assert result == {
        "ok": True,
        "bucket": "photos",
        "key": "2025/a b.jpg",
        "previous_key": "2024/a b.jpg",
        "overwrite": False,
        "region": "eu-central-1",
    }


@respx.mock
async def test_object_rename_with_overwrite_sends_true(rest_server):
    route = respx.route(method="MOVE").mock(return_value=httpx.Response(200))
    await call(
        rest_server,
        "object_rename",
        bucket="photos",
        key="a",
        destination="b",
        overwrite=True,
        region="eu-central-1",
        confirm=True,
    )
    assert route.calls.last.request.headers["overwrite"] == "true"


@respx.mock
async def test_object_rename_reaches_the_real_regional_endpoint():
    settings = Settings(
        access_key_id=FAKE_ACCESS_KEY,
        secret_access_key=FAKE_SECRET_KEY,
        region="us-east-1",
        read_only=False,
    )
    route = respx.route(method="MOVE").mock(return_value=httpx.Response(200))
    await call(
        build_server(settings),
        "object_rename",
        bucket="photos",
        key="a.jpg",
        destination="b.jpg",
        region="eu-central-1",
        confirm=True,
    )
    assert str(route.calls.last.request.url) == "https://s3.eu-central-1.wasabisys.com/photos/a.jpg"


@respx.mock
async def test_object_rename_refuses_without_confirm(rest_server):
    route = respx.route().mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match="RenameObject refused: this is destructive"):
        await call(
            rest_server, "object_rename", bucket="b", key="a", destination="c", region="us-east-1"
        )
    assert not route.called


@respx.mock
async def test_object_rename_refuses_when_read_only(read_only_server):
    route = respx.route().mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match="RenameObject refused: the server is read-only"):
        await call(
            read_only_server,
            "object_rename",
            bucket="b",
            key="a",
            destination="c",
            region="us-east-1",
            confirm=True,
        )
    assert not route.called


@respx.mock
async def test_object_rename_reports_wasabi_error_code_and_message(rest_server):
    respx.route(method="MOVE").mock(
        return_value=_error("ObjectExists", "The destination object already exists")
    )
    with pytest.raises(ToolError) as excinfo:
        await call(
            rest_server,
            "object_rename",
            bucket="b",
            key="a",
            destination="c",
            region="us-east-1",
            confirm=True,
        )
    assert str(excinfo.value).endswith(
        "RenameObject failed: ObjectExists: The destination object already exists"
    )


@respx.mock
async def test_object_rename_reports_per_key_errors_in_a_200(rest_server):
    respx.route(method="MOVE").mock(return_value=httpx.Response(200, content=MOVE_RESULT))
    with pytest.raises(ToolError, match="RenameObject failed: .*ObjectExists"):
        await call(
            rest_server,
            "object_rename",
            bucket="b",
            key="logs/b.txt",
            destination="archive/b.txt",
            region="us-east-1",
            confirm=True,
        )


# object_rename_prefix


@respx.mock
async def test_object_rename_prefix_sends_prefix_headers_and_parses_results(rest_server):
    route = respx.route(method="MOVE", host="s3.wasabi.test").mock(
        return_value=httpx.Response(200, content=MOVE_RESULT)
    )
    result = await call(
        rest_server,
        "object_rename_prefix",
        bucket="data",
        prefix="logs/",
        destination="archive/",
        region="eu-central-1",
        confirm=True,
    )
    request = route.calls.last.request
    assert request.method == "MOVE"
    assert str(request.url) == f"{S3}/data/logs/"
    assert request.headers["destination"] == "archive/"
    assert request.headers["overwrite"] == "false"
    assert request.headers["x-wasabi-prefix"] == "true"
    assert request.headers["x-wasabi-quiet"] == "false"
    _assert_signed(request)
    assert "x-wasabi-prefix" in request.headers["authorization"]
    assert result["source_prefix"] == "logs/"
    assert result["destination_prefix"] == "archive/"
    assert result["move_count"] == 1
    assert result["moved"] == [
        {"source_key": "logs/a.txt", "destination_key": "archive/a.txt", "version_id": "v1"}
    ]
    assert result["errors"] == [
        {
            "source_key": "logs/b.txt",
            "destination_key": "archive/b.txt",
            "error": {"code": "ObjectExists", "message": "The destination exists"},
        }
    ]


@respx.mock
async def test_object_rename_prefix_passes_overwrite_false_without_refusing(rest_server):
    route = respx.route(method="MOVE").mock(return_value=httpx.Response(200))
    result = await call(
        rest_server,
        "object_rename_prefix",
        bucket="data",
        prefix="logs/",
        destination="archive/",
        quiet=True,
        region="us-east-1",
        confirm=True,
    )
    assert route.calls.last.request.headers["overwrite"] == "false"
    assert route.calls.last.request.headers["x-wasabi-quiet"] == "true"
    assert result["moved"] == [] and result["errors"] == []


@respx.mock
async def test_object_rename_prefix_refuses_without_confirm(rest_server):
    route = respx.route().mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match="RenameObjectPrefix refused: this is destructive"):
        await call(
            rest_server,
            "object_rename_prefix",
            bucket="data",
            prefix="logs/",
            destination="archive/",
            region="us-east-1",
        )
    assert not route.called


@respx.mock
async def test_object_rename_prefix_refuses_when_read_only(read_only_server):
    route = respx.route().mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match="RenameObjectPrefix refused: the server is read-only"):
        await call(
            read_only_server,
            "object_rename_prefix",
            bucket="data",
            prefix="logs/",
            destination="archive/",
            region="us-east-1",
            confirm=True,
        )
    assert not route.called


@respx.mock
async def test_object_rename_prefix_refuses_an_empty_prefix(rest_server):
    route = respx.route().mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match="prefix must not be empty"):
        await call(
            rest_server,
            "object_rename_prefix",
            bucket="data",
            prefix="",
            destination="archive/",
            region="us-east-1",
            confirm=True,
        )
    assert not route.called


@respx.mock
async def test_object_rename_prefix_reports_wasabi_error(rest_server):
    respx.route(method="MOVE").mock(return_value=_error("AccessDenied", "Access Denied"))
    with pytest.raises(ToolError, match="RenameObjectPrefix failed: AccessDenied: Access Denied"):
        await call(
            rest_server,
            "object_rename_prefix",
            bucket="data",
            prefix="logs/",
            destination="archive/",
            region="us-east-1",
            confirm=True,
        )


# object_compose


@respx.mock
async def test_object_compose_puts_the_ordered_source_list(rest_server):
    route = respx.route(method="PUT", host="s3.wasabi.test").mock(
        return_value=httpx.Response(200, headers={"ETag": '"abc-3"', "x-amz-version-id": "v9"})
    )
    result = await call(
        rest_server,
        "object_compose",
        bucket="data",
        key="joined.bin",
        sources=["part-1", "part-2", "part 3"],
        region="eu-central-1",
    )
    request = route.calls.last.request
    assert request.method == "PUT"
    assert str(request.url) == f"{S3}/data/joined.bin?compose"
    assert request.headers["content-type"] == "text/xml"
    _assert_signed(request)
    root = ElementTree.fromstring(request.content)
    assert root.tag == "ComposeRequest"
    assert [c.findtext("Key") for c in root.findall("Component")] == ["part-1", "part-2", "part 3"]
    assert result == {
        "ok": True,
        "bucket": "data",
        "key": "joined.bin",
        "sources": ["part-1", "part-2", "part 3"],
        "etag": "abc-3",
        "version_id": "v9",
        "region": "eu-central-1",
    }


@pytest.mark.parametrize(
    ("sources", "message"),
    [
        ([], "give 1 to 32 source keys, not 0"),
        ([f"k{i}" for i in range(33)], "give 1 to 32 source keys, not 33"),
        (["a", ""], "every source key must be a non-empty string"),
    ],
)
@respx.mock
async def test_object_compose_validates_sources_before_signing(rest_server, sources, message):
    route = respx.route().mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match=message):
        await call(
            rest_server, "object_compose", bucket="d", key="k", sources=sources, region="us-east-1"
        )
    assert not route.called


@respx.mock
async def test_object_compose_accepts_32_sources(rest_server):
    route = respx.route(method="PUT").mock(return_value=httpx.Response(200))
    sources = [f"k{i}" for i in range(32)]
    await call(
        rest_server, "object_compose", bucket="d", key="k", sources=sources, region="us-east-1"
    )
    assert len(ElementTree.fromstring(route.calls.last.request.content)) == 32


@respx.mock
async def test_object_compose_refuses_when_read_only(read_only_server):
    route = respx.route().mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match="ComposeObject refused: the server is read-only"):
        await call(
            read_only_server,
            "object_compose",
            bucket="d",
            key="k",
            sources=["a"],
            region="us-east-1",
        )
    assert not route.called


@respx.mock
async def test_object_compose_reports_wasabi_error(rest_server):
    respx.route(method="PUT").mock(
        return_value=_error("NoSuchKey", "The specified key does not exist.")
    )
    with pytest.raises(ToolError) as excinfo:
        await call(
            rest_server, "object_compose", bucket="d", key="k", sources=["a"], region="us-east-1"
        )
    assert str(excinfo.value).endswith(
        "ComposeObject failed: NoSuchKey: The specified key does not exist."
    )


# object_append


@respx.mock
async def test_object_append_puts_the_text(rest_server):
    route = respx.route(method="PUT", host="s3.wasabi.test").mock(
        return_value=httpx.Response(200, headers={"ETag": '"e1-2"'})
    )
    result = await call(
        rest_server,
        "object_append",
        bucket="logs",
        key="app.log",
        text="héllo\n",
        region="eu-central-1",
    )
    request = route.calls.last.request
    assert request.method == "PUT"
    assert str(request.url) == f"{S3}/logs/app.log?append"
    assert request.content == "héllo\n".encode()
    assert request.headers["content-type"] == "text/plain"
    _assert_signed(request)
    assert result["ok"] is True
    assert result["etag"] == "e1-2"
    assert result["appended_bytes"] == 7


@respx.mock
async def test_object_append_sends_the_content_type_and_wasabi_headers(rest_server):
    route = respx.route(method="PUT").mock(
        return_value=httpx.Response(200, headers={"X-Wasabi-Append-Position": "42"})
    )
    result = await call(
        rest_server,
        "object_append",
        bucket="logs",
        key="app.csv",
        text="a,b\n",
        content_type="text/csv",
        region="us-east-1",
    )
    assert route.calls.last.request.headers["content-type"] == "text/csv"
    assert result["append_position"] == "42"


@respx.mock
async def test_object_append_refuses_more_than_1_mib(rest_server):
    route = respx.route().mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match="the limit is 1048576 bytes"):
        await call(
            rest_server,
            "object_append",
            bucket="logs",
            key="k",
            text="x" * (1024 * 1024 + 1),
            region="us-east-1",
        )
    assert not route.called


@respx.mock
async def test_object_append_refuses_when_read_only(read_only_server):
    route = respx.route().mock(return_value=httpx.Response(200))
    with pytest.raises(ToolError, match="AppendObject refused: the server is read-only"):
        await call(
            read_only_server, "object_append", bucket="l", key="k", text="x", region="us-east-1"
        )
    assert not route.called


@respx.mock
async def test_object_append_reports_wasabi_error(rest_server):
    respx.route(method="PUT").mock(
        return_value=_error("TooManyParts", "The object has too many appends")
    )
    with pytest.raises(ToolError, match="AppendObject failed: TooManyParts: The object has too"):
        await call(rest_server, "object_append", bucket="l", key="k", text="x", region="us-east-1")


# Region lookup and secrets


@respx.mock
async def test_writes_use_the_bucket_region_from_get_bucket_location(settings, moto_url):
    boto3.client(
        "s3",
        region_name="eu-central-1",
        endpoint_url=moto_url,
        aws_access_key_id=FAKE_ACCESS_KEY,
        aws_secret_access_key=FAKE_SECRET_KEY,
    ).create_bucket(
        Bucket="eu-data", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
    )
    host = urlsplit(moto_url).netloc
    route = respx.route(method="PUT", host=host.split(":")[0]).mock(
        return_value=httpx.Response(200)
    )
    server = build_server(settings)
    await call(server, "object_append", bucket="eu-data", key="k", text="x")
    await call(server, "object_compose", bucket="eu-data", key="j", sources=["k"])
    assert route.call_count == 2
    for call_ in route.calls:
        _assert_signed(call_.request, "eu-central-1")


@respx.mock
async def test_results_never_contain_secrets(rest_server):
    respx.route().mock(return_value=httpx.Response(200, content=MOVE_RESULT))
    result = await call(
        rest_server,
        "object_rename_prefix",
        bucket="data",
        prefix="logs/",
        destination="archive/",
        region="us-east-1",
        confirm=True,
    )
    assert not any(secret in str(result) for secret in SECRETS)

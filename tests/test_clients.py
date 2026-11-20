from wasabi_mcp.clients import Clients


def test_s3_client_reaches_moto(settings):
    s3 = Clients(settings).s3()
    s3.create_bucket(Bucket="wasabi-mcp-test-clients")
    names = [bucket["Name"] for bucket in s3.list_buckets()["Buckets"]]
    assert names == ["wasabi-mcp-test-clients"]


def test_moto_is_reset_between_tests(settings):
    assert Clients(settings).s3().list_buckets()["Buckets"] == []


def test_iam_and_sts_clients_reach_moto(settings):
    clients = Clients(settings)
    clients.iam().create_user(UserName="wasabi-mcp-test-user")
    assert [u["UserName"] for u in clients.iam().list_users()["Users"]] == ["wasabi-mcp-test-user"]
    assert clients.sts().get_caller_identity()["Account"]


def test_clients_are_cached_per_region(settings):
    clients = Clients(settings)
    assert clients.s3() is clients.s3()
    assert clients.s3() is clients.s3(settings.region)
    assert clients.s3("eu-central-1") is not clients.s3()
    assert clients.iam() is clients.iam()
    assert clients.sts() is clients.sts()


def test_client_configuration(settings):
    clients = Clients(settings)
    s3 = clients.s3("eu-central-1")
    assert s3.meta.region_name == "eu-central-1"
    assert s3.meta.config.s3["addressing_style"] == "path"
    assert s3.meta.config.retries["mode"] == "standard"
    assert clients.iam().meta.region_name == "us-east-1"
    assert clients.sts().meta.region_name == "us-east-1"


def test_every_client_only_uses_flexible_checksums_when_required(settings):
    # Wasabi doesn't support AWS flexible checksums (issue 18).
    clients = Clients(settings)
    for client in (clients.s3(), clients.s3("eu-central-1"), clients.iam(), clients.sts()):
        assert client.meta.config.response_checksum_validation == "when_required"
        assert client.meta.config.request_checksum_calculation == "when_required"

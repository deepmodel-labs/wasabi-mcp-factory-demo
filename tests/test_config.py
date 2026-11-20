import dataclasses

import pytest

from wasabi_mcp.config import Settings, parse_bool


def test_defaults_point_at_wasabi_and_start_read_only():
    settings = Settings.from_env({})
    assert settings.region == "us-east-1"
    assert settings.read_only is True
    assert settings.s3_endpoint_for() == "https://s3.us-east-1.wasabisys.com"
    assert settings.s3_endpoint_for("eu-central-2") == "https://s3.eu-central-2.wasabisys.com"
    assert settings.iam_url == "https://iam.wasabisys.com"
    assert settings.sts_url == "https://sts.wasabisys.com"
    assert settings.stats_url == "https://stats.wasabisys.com"
    assert settings.wacm_url == "https://api.wacm.wasabisys.com/api"
    assert not settings.has_keys
    assert not settings.has_wacm


def test_region_sets_default_s3_endpoint():
    settings = Settings.from_env({"WASABI_REGION": "ap-northeast-1"})
    assert settings.s3_endpoint_for() == "https://s3.ap-northeast-1.wasabisys.com"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("False", False),
        ("0", False),
        ("no", False),
        (" no ", False),
    ],
)
def test_read_only_parsing(raw, expected):
    assert Settings.from_env({"WASABI_MCP_READ_ONLY": raw}).read_only is expected


def test_empty_read_only_keeps_the_default():
    assert Settings.from_env({"WASABI_MCP_READ_ONLY": ""}).read_only is True


def test_invalid_bool_is_rejected():
    with pytest.raises(ValueError, match="WASABI_MCP_READ_ONLY"):
        parse_bool("WASABI_MCP_READ_ONLY", "maybe", True)


def test_overrides_replace_every_default():
    settings = Settings.from_env(
        {
            "WASABI_S3_ENDPOINT": "http://localhost:9000/",
            "WASABI_IAM_ENDPOINT": "http://iam.local",
            "WASABI_STS_ENDPOINT": "http://sts.local",
            "WASABI_STATS_ENDPOINT": "http://stats.local",
            "WASABI_WACM_ENDPOINT": "http://wacm.local/api",
        }
    )
    assert settings.s3_endpoint_for() == "http://localhost:9000"
    assert settings.s3_endpoint_for("eu-west-1") == "http://localhost:9000"
    assert settings.iam_url == "http://iam.local"
    assert settings.sts_url == "http://sts.local"
    assert settings.stats_url == "http://stats.local"
    assert settings.wacm_url == "http://wacm.local/api"


def test_has_keys_and_has_wacm_need_both_halves():
    assert not Settings.from_env({"WASABI_ACCESS_KEY_ID": "AK"}).has_keys
    assert Settings.from_env(
        {"WASABI_ACCESS_KEY_ID": "AK", "WASABI_SECRET_ACCESS_KEY": "SK"}
    ).has_keys
    assert not Settings.from_env({"WASABI_WACM_USERNAME": "user"}).has_wacm
    assert Settings.from_env(
        {"WASABI_WACM_USERNAME": "user", "WASABI_WACM_API_KEY": "key"}
    ).has_wacm


def test_credentials_are_absent_from_repr():
    settings = Settings.from_env(
        {
            "WASABI_ACCESS_KEY_ID": "AKIDVALUE",
            "WASABI_SECRET_ACCESS_KEY": "SECRETVALUE",
            "WASABI_WACM_USERNAME": "WACMUSERVALUE",
            "WASABI_WACM_API_KEY": "WACMKEYVALUE",
        }
    )
    text = repr(settings)
    for value in ("AKIDVALUE", "SECRETVALUE", "WACMUSERVALUE", "WACMKEYVALUE"):
        assert value not in text


def test_settings_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Settings().read_only = False  # type: ignore[misc]

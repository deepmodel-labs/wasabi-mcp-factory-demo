"""Live scenarios S1, S2, S4 and S5 (PRD section 3), plus the Stats summary (8.4)."""

from __future__ import annotations

import json

import pytest

from tests.helpers import call
from tests.live.support import fetch, read_only_policy

pytestmark = pytest.mark.live


async def test_s1_buckets_and_how_much_each_stores(live_server, bucket):
    listed = await call(live_server, "bucket_list", max_items=1_000, include_region=True)
    assert bucket in [item["name"] for item in listed["items"]]
    # Stats lag about a day, so a new account or bucket may have no records yet:
    # check the shape, not the contents.
    usage = await call(live_server, "stats_bucket_utilization_list", latest=True)
    assert isinstance(usage["items"], list)
    assert "truncated" in usage


async def test_stats_usage_summary(live_server):
    summary = await call(live_server, "stats_usage_summary", top_n=5)
    assert isinstance(summary["top_buckets"], list)
    assert len(summary["top_buckets"]) <= 5
    assert "bucket_count" in summary


async def test_s2_presigned_links_for_every_mp4_under_media(live_server, bucket):
    mp4s = ["media/one.mp4", "media/two.mp4"]
    for key in [*mp4s, "media/readme.txt"]:
        await call(
            live_server,
            "object_put_text",
            bucket=bucket,
            key=key,
            text=key,
            content_type="video/mp4" if key.endswith(".mp4") else "text/plain",
        )
    result = await call(
        live_server, "presign_get_for_prefix", bucket=bucket, prefix="media/", suffix=".mp4"
    )
    assert sorted(item["key"] for item in result["items"]) == mp4s
    assert result["truncated"] is False
    for item in result["items"]:
        assert fetch(item["url"]) == item["key"].encode()


async def test_s4_buckets_without_policy_and_policies_granting_write(live_server, bucket, new_name):
    listed = await call(live_server, "bucket_list", max_items=1_000)
    assert bucket in [item["name"] for item in listed["items"]]
    assert await call(live_server, "bucket_policy_get", bucket=bucket) == {"configured": False}

    policy_name = new_name("policy")
    document = read_only_policy(bucket)
    document["Statement"].append(
        {"Effect": "Allow", "Action": "s3:PutObject", "Resource": f"arn:aws:s3:::{bucket}/*"}
    )
    created = await call(
        live_server,
        "iam_policy_create",
        policy_name=policy_name,
        policy_document=document,
        path="/wasabi-mcp-test/",
        confirm=True,
    )
    arn = created["policy"]["Arn"]
    policies = await call(
        live_server, "iam_policy_list", scope="Local", path_prefix="/wasabi-mcp-test/"
    )
    assert arn in [policy["Arn"] for policy in policies["items"]]
    got = await call(live_server, "iam_policy_get", policy_arn=arn)
    granting_write = [
        statement
        for statement in got["document"]["Statement"]
        if "s3:PutObject" in json.dumps(statement["Action"])
        and bucket in json.dumps(statement["Resource"])
    ]
    assert granting_write
    await call(live_server, "iam_policy_delete", policy_arn=arn, confirm=True)


async def test_s5_ninety_day_expiry_under_tmp_in_dev_buckets(live_server, bucket):
    await call(live_server, "bucket_tagging_set", bucket=bucket, tags={"env": "dev"})
    tags = await call(live_server, "bucket_tagging_get", bucket=bucket)
    assert tags["tags"]["env"] == "dev"

    rule = {
        "ID": "tmp-90-days",
        "Status": "Enabled",
        "Filter": {"Prefix": "tmp/"},
        "Expiration": {"Days": 90},
    }
    await call(live_server, "bucket_lifecycle_set", bucket=bucket, rules=[rule], confirm=True)
    got = await call(live_server, "bucket_lifecycle_get", bucket=bucket)
    [applied] = got["rules"]
    assert applied["Expiration"]["Days"] == 90
    assert json.dumps(applied.get("Filter", {})).count("tmp/") == 1

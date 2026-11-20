"""Access tools: bucket policies and bucket and object ACLs.

Changes go to the bucket's own region (PRD 5.3) through a private ``_bucket_client`` that
looks the region up with GetBucketLocation and caches it. Each module that changes buckets
has its own copy, so no shared file is edited (PRD 8.7).
"""

from __future__ import annotations

import json
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from mcp.server.mcpserver import MCPServer

from wasabi_mcp.errors import explain
from wasabi_mcp.safety import ToolRefused, require_confirmed
from wasabi_mcp.tools import DESTRUCTIVE, READ, ToolContext, clean

DEFAULT_REGION = "us-east-1"
CANNED_ACLS = ("private", "public-read", "public-read-write", "authenticated-read")
PERMISSIONS = ("FULL_CONTROL", "WRITE", "WRITE_ACP", "READ", "READ_ACP")
# Which grant field names the grantee, and the S3 grantee type it maps to.
GRANTEE_FIELDS = {
    "id": ("CanonicalUser", "ID"),
    "uri": ("Group", "URI"),
    "email": ("AmazonCustomerByEmail", "EmailAddress"),
}


def _code(exc: ClientError) -> str | None:
    return exc.response.get("Error", {}).get("Code")


def policy_document(policy: dict[str, Any] | str) -> str:
    """``policy`` (a dict or a JSON string) as compact JSON, refusing anything that isn't a
    JSON object."""
    if isinstance(policy, str):
        try:
            policy = json.loads(policy)
        except json.JSONDecodeError as exc:
            raise ToolRefused(f"policy isn't valid JSON: {exc}") from exc
    if not isinstance(policy, dict):
        raise ToolRefused("policy must be a JSON object with Version and Statement")
    if "Statement" not in policy:
        raise ToolRefused("policy has no Statement")
    return json.dumps(policy, separators=(",", ":"))


def acl_arguments(
    canned_acl: str | None, grants: list[dict[str, str]] | None
) -> tuple[str | None, list[dict[str, Any]] | None]:
    """Validate a canned ACL or explicit grants (exactly one) and turn grants into S3 form.

    A grant is ``{"permission": ..., "id"|"uri"|"email": ...}``.
    """
    if (canned_acl is None) == (grants is None):
        raise ToolRefused("Give exactly one of canned_acl or grants.")
    if canned_acl is not None:
        if canned_acl not in CANNED_ACLS:
            raise ToolRefused(
                f"canned_acl must be one of {', '.join(CANNED_ACLS)}, not {canned_acl!r}"
            )
        return canned_acl, None
    converted = []
    for grant in grants or []:
        permission = grant.get("permission")
        if permission not in PERMISSIONS:
            raise ToolRefused(
                f"grant permission must be one of {', '.join(PERMISSIONS)}, not {permission!r}"
            )
        named = [field for field in GRANTEE_FIELDS if grant.get(field)]
        if len(named) != 1:
            raise ToolRefused("each grant needs exactly one of id, uri or email")
        kind, attribute = GRANTEE_FIELDS[named[0]]
        converted.append(
            {"Grantee": {"Type": kind, attribute: grant[named[0]]}, "Permission": permission}
        )
    return None, converted


def _acl(response: dict[str, Any]) -> dict[str, Any]:
    return {"owner": clean(response.get("Owner")), "grants": clean(response.get("Grants", []))}


def register(server: MCPServer, ctx: ToolContext) -> None:
    settings = ctx.settings
    regions: dict[str, str] = {}

    def _bucket_client(bucket: str, region: str | None) -> Any:
        """The S3 client for ``bucket``'s own region."""
        if region:
            return ctx.clients.s3(region)
        if bucket not in regions:
            try:
                response = ctx.clients.s3().get_bucket_location(Bucket=bucket)
            except (ClientError, BotoCoreError) as exc:
                raise explain(exc, "GetBucketLocation") from exc
            regions[bucket] = response.get("LocationConstraint") or DEFAULT_REGION
        return ctx.clients.s3(regions[bucket])

    @server.tool(name="bucket_policy_get", annotations=READ)
    def bucket_policy_get(bucket: str, region: str | None = None) -> dict[str, Any]:
        """Show a bucket's policy document as JSON, or {"configured": false} when the bucket
        has no policy."""
        try:
            response = ctx.clients.s3(region).get_bucket_policy(Bucket=bucket)
        except ClientError as exc:
            if _code(exc) == "NoSuchBucketPolicy":
                return {"configured": False}
            raise explain(exc, "GetBucketPolicy") from exc
        except BotoCoreError as exc:
            raise explain(exc, "GetBucketPolicy") from exc
        return {"configured": True, "bucket": bucket, "policy": json.loads(response["Policy"])}

    @server.tool(name="bucket_policy_set", annotations=DESTRUCTIVE)
    def bucket_policy_set(
        bucket: str,
        policy: dict[str, Any] | str,
        region: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Replace a bucket's policy with policy (a dict or JSON string, validated first).
        Account and canonical IDs in principals must be Wasabi's, not AWS's. Needs
        confirm=true."""
        require_confirmed(settings, "PutBucketPolicy", confirm)
        document = policy_document(policy)
        try:
            _bucket_client(bucket, region).put_bucket_policy(Bucket=bucket, Policy=document)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "PutBucketPolicy") from exc
        return {"ok": True, "bucket": bucket, "policy": json.loads(document)}

    @server.tool(name="bucket_policy_delete", annotations=DESTRUCTIVE)
    def bucket_policy_delete(
        bucket: str, region: str | None = None, confirm: bool = False
    ) -> dict[str, Any]:
        """Delete a bucket's policy, removing every access it granted. Needs confirm=true."""
        require_confirmed(settings, "DeleteBucketPolicy", confirm)
        try:
            _bucket_client(bucket, region).delete_bucket_policy(Bucket=bucket)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "DeleteBucketPolicy") from exc
        return {"ok": True, "bucket": bucket}

    @server.tool(name="bucket_acl_get", annotations=READ)
    def bucket_acl_get(bucket: str, region: str | None = None) -> dict[str, Any]:
        """Show a bucket's ACL: its owner and the list of grants."""
        try:
            response = ctx.clients.s3(region).get_bucket_acl(Bucket=bucket)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "GetBucketAcl") from exc
        return {"bucket": bucket, **_acl(response)}

    @server.tool(name="bucket_acl_set", annotations=DESTRUCTIVE)
    def bucket_acl_set(
        bucket: str,
        canned_acl: str | None = None,
        grants: list[dict[str, str]] | None = None,
        region: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Replace a bucket's ACL with a canned_acl (private, public-read, public-read-write,
        authenticated-read) or explicit grants ({"permission", and one of "id", "uri",
        "email"}), and return the new ACL. Trial accounts and accounts created after
        2023-03-13 can't make buckets public. Needs confirm=true."""
        require_confirmed(settings, "PutBucketAcl", confirm)
        canned, converted = acl_arguments(canned_acl, grants)
        client = _bucket_client(bucket, region)
        try:
            if canned is not None:
                client.put_bucket_acl(Bucket=bucket, ACL=canned)
            else:
                owner = client.get_bucket_acl(Bucket=bucket)["Owner"]
                client.put_bucket_acl(
                    Bucket=bucket, AccessControlPolicy={"Owner": owner, "Grants": converted}
                )
            response = client.get_bucket_acl(Bucket=bucket)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "PutBucketAcl") from exc
        return {"ok": True, "bucket": bucket, **_acl(response)}

    @server.tool(name="object_acl_get", annotations=READ)
    def object_acl_get(
        bucket: str, key: str, version_id: str | None = None, region: str | None = None
    ) -> dict[str, Any]:
        """Show an object's ACL (optionally of one version): its owner and grants."""
        params = {"Bucket": bucket, "Key": key}
        if version_id:
            params["VersionId"] = version_id
        try:
            response = ctx.clients.s3(region).get_object_acl(**params)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "GetObjectAcl") from exc
        return {"bucket": bucket, "key": key, "version_id": version_id, **_acl(response)}

    @server.tool(name="object_acl_set", annotations=DESTRUCTIVE)
    def object_acl_set(
        bucket: str,
        key: str,
        canned_acl: str | None = None,
        grants: list[dict[str, str]] | None = None,
        version_id: str | None = None,
        region: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Replace an object's (or one version's) ACL with a canned_acl or explicit grants,
        as for bucket_acl_set, and return the new ACL. Trial accounts and accounts created
        after 2023-03-13 can't make objects public. Needs confirm=true."""
        require_confirmed(settings, "PutObjectAcl", confirm)
        canned, converted = acl_arguments(canned_acl, grants)
        client = _bucket_client(bucket, region)
        params = {"Bucket": bucket, "Key": key}
        if version_id:
            params["VersionId"] = version_id
        try:
            if canned is not None:
                client.put_object_acl(**params, ACL=canned)
            else:
                owner = client.get_object_acl(**params)["Owner"]
                client.put_object_acl(
                    **params, AccessControlPolicy={"Owner": owner, "Grants": converted}
                )
            response = client.get_object_acl(**params)
        except (ClientError, BotoCoreError) as exc:
            raise explain(exc, "PutObjectAcl") from exc
        return {
            "ok": True,
            "bucket": bucket,
            "key": key,
            "version_id": version_id,
            **_acl(response),
        }

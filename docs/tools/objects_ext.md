# Wasabi object extensions (`objects_ext.py`)

Wasabi-only object operations with no AWS S3 equivalent (PRD 6.4). Every call is a path-style,
SigV4-signed request to `https://s3.<region>.wasabisys.com/<bucket>/<key>` made with
`rest.signed_s3`. When `region` isn't given, the bucket's region is looked up once with
GetBucketLocation and cached.

| Name | Kind | Description |
|---|---|---|
| `object_rename` | D | Rename an object (all its versions) to the `destination` key in the same bucket: `MOVE /<bucket>/<key>` with `Destination: <destination>` and `Overwrite: true\|false`. `overwrite` defaults to false, so an existing destination isn't replaced. Needs `s3:PutObject` and `confirm=true`. |
| `object_rename_prefix` | D | Rename every key (all versions) under `prefix` so it starts with `destination` instead: `MOVE /<bucket>/<prefix>` with `X-Wasabi-Prefix: true`, `Overwrite` and `X-Wasabi-Quiet`. Returns Wasabi's per-key results as `moved` and `errors`, plus `source_prefix`, `destination_prefix` and `move_count`. With `overwrite=false`, keys whose destination exists are reported under `errors`; nothing is refused locally. Needs `confirm=true`. |
| `object_compose` | W | Create `key` by linking 1 to 32 `sources` in the same bucket, in order: `PUT /<bucket>/<key>?compose` with a `<ComposeRequest>` body. No data is copied. Customer-provided (SSE-C) keys aren't supported, and the returned ETag is a hash of the part MD5s, not the MD5 of the whole object. |
| `object_append` | W | Append `text` (at most 1 MiB as UTF-8) to an object: `PUT /<bucket>/<key>?append` with `Content-Type` (default `text/plain`). Returns the ETag, version ID and any `X-Wasabi-*` headers. An object can be appended to at most 1,023 times, and each append is billed as a small object. |

## Safety

- `object_rename` and `object_rename_prefix` are destructive: they refuse in read-only mode and
  without `confirm=true`, before any request is sent.
- `object_compose` and `object_append` refuse in read-only mode. Source counts (1 to 32), empty
  source keys, an empty prefix and appends over 1 MiB are refused before signing.
- A non-2xx response comes back as `ToolRefused("<Action> failed: <Code>: <Message>")` with
  Wasabi's own error code and message. The actions are `RenameObject`, `RenameObjectPrefix`,
  `ComposeObject` and `AppendObject`.

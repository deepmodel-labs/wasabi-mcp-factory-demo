# Server (`meta.py`)

| Tool | Kind | Description |
|---|---|---|
| `server_info` | R | The server's version, default region, read-only flag, endpoints, whether Wasabi keys and WACM Connect are configured, whether new secrets are written to `WASABI_MCP_SECRETS_DIR` (`secrets_dir_enabled`), and the loaded tool groups. Never returns credentials or the secrets directory path. |

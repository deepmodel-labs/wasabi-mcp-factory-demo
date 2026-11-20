# WACM Connect: channel accounts and their users (`wacm_channels.py`, 8 tools)

All tools call WACM Connect with HTTP Basic auth (`WASABI_WACM_USERNAME`, `WASABI_WACM_API_KEY`). When either variable is missing, every tool refuses with a message naming both.

- **Lists** take `page` (numbered from 0) and `size` and return `{"items", "next_token", "truncated"}` plus WACM's own paging fields such as `total`. `next_token` is the next page number, to pass back as `page`.
- **Filters** are sent as WACM's camelCase query parameters; `from_date` and `to_date` are sent as `from` and `to`. Parameters left unset aren't sent.
- **Errors:** WACM's `{error, code}` comes back as `HTTP <status>: <error>`. HTTP 429 (rate limit) is retried once after 5 seconds, then reported.
- **Kinds:** R reads; W refuses while the server is read-only; D also needs `confirm=true`. Guards run before any request.

| Tool | Kind | Parameters | Description |
|---|---|---|---|
| `wacm_channel_account_list` | R | `page`?, `size`? | List WACM Connect channel accounts. Returns one page (pages start at 0); next_token is the next page number. |
| `wacm_channel_account_get` | R | `channel_account_id` | Get one WACM Connect channel account by ID. |
| `wacm_channel_account_create` | W | `name`, `contact_email`, `purchased_storage`?, `storage_quota_type`? | Create a WACM Connect channel account with an optional purchased storage amount (TB) and Soft or Hard quota. Returns the new channel account. |
| `wacm_channel_account_update` | W | `channel_account_id`, `name`?, `contact_email`?, `purchased_storage`?, `storage_quota_type`? | Update a WACM Connect channel account's name, contact email, purchased storage (TB) or quota type. Only the fields given are sent. Returns the updated account. |
| `wacm_channel_account_delete` | D | `channel_account_id`, `confirm`? | Delete a WACM Connect channel account. This also unlinks all of its sub-accounts. Needs confirm=true. |
| `wacm_channel_user_create` | W | `channel_account_id`, `email`, `first_name`, `last_name`, `role`, `username`?, `password`? | Create a user on a WACM Connect channel account with the given role. Returns the new user (never its password). |
| `wacm_channel_user_update` | D | `user_id`, `role`?, `active`?, `reset_mfa`?, `password`?, `confirm`? | Change a channel account user's role, enable or disable them, reset their MFA or set a new password. Changes access, so it needs confirm=true. |
| `wacm_channel_user_delete` | D | `user_id`, `confirm`? | Delete a channel account user. Needs confirm=true. |

Parameters marked `?` are optional.

## Safety notes

- `wacm_channel_account_create`, `_update` and `wacm_channel_user_create` are W: they refuse while the server is read-only.
- `wacm_channel_account_delete` also unlinks every sub-account of the channel account.
- `wacm_channel_user_update` is D because it changes access (role, active, MFA, password).
- `wacm_channel_account_update` refuses when no field to change is given.
- Passwords are sent but never returned.

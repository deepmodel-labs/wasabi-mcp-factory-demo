# WACM Connect: members (`wacm_members.py`, 5 tools)

All tools call WACM Connect with HTTP Basic auth (`WASABI_WACM_USERNAME`, `WASABI_WACM_API_KEY`). When either variable is missing, every tool refuses with a message naming both.

- **Lists** take `page` (numbered from 0) and `size` and return `{"items", "next_token", "truncated"}` plus WACM's own paging fields such as `total`. `next_token` is the next page number, to pass back as `page`.
- **Filters** are sent as WACM's camelCase query parameters; `from_date` and `to_date` are sent as `from` and `to`. Parameters left unset aren't sent.
- **Errors:** WACM's `{error, code}` comes back as `HTTP <status>: <error>`. HTTP 429 (rate limit) is retried once after 5 seconds, then reported.
- **Kinds:** R reads; W refuses while the server is read-only; D also needs `confirm=true`. Guards run before any request.

| Tool | Kind | Parameters | Description |
|---|---|---|---|
| `wacm_member_list` | R | `page`?, `size`?, `id`?, `status`?, `username`?, `sub_account_id`? | List WACM Connect members, optionally filtered by ID, status, username or sub-account. Returns one page (pages start at 0). |
| `wacm_member_get` | R | `member_id` | Get one WACM Connect member by ID. |
| `wacm_member_create` | D | `first_name`, `last_name`, `email`, `password`, `member_role`, `username`?, `password_reset_required`?, `confirm`? | Create a WACM Connect member with the given role, granting them access. Returns the new member (never its password). Needs confirm=true. |
| `wacm_member_update` | D | `member_id`, `member_role`?, `reset_mfa`?, `password`?, `active`?, `reinstate`?, `confirm`? | Change a WACM Connect member's role, reset their MFA, set a password, enable or disable them, or reinstate them. Only the fields given are sent. Needs confirm=true. |
| `wacm_member_delete` | D | `member_id`, `confirm`? | Delete a WACM Connect member. Needs confirm=true. |

Parameters marked `?` are optional.

## Safety notes

- `wacm_member_create`, `_update` and `_delete` are D (they grant, change or remove access): they need writes enabled and `confirm=true`.
- `wacm_member_update` refuses when no field to change is given.
- Passwords are sent but never returned.

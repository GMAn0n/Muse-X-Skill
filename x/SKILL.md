---
name: "x"
description: "Work with the user's X (Twitter) account: publish posts, read timelines and mentions, search recent posts, and manage likes, reposts, bookmarks, and follows. One-time `auth login` handles OAuth; tokens auto-refresh. Use when the user asks about X, Twitter, tweeting, or his X activity."
---

# X (Twitter)

## Purpose
Operate the user's X account. Jobs: `auth` (one-time login, tokens auto-refresh),
`me` (identity), `post` (publish; approval-gated), `delete`, `timeline`,
`mentions`, `user-tweets`, `search` (7-day recent), `user` (lookup),
`like`/`unlike`, `repost`/`unrepost`, `bookmark`/`unbookmark`,
`follow`/`unfollow`, `followers`/`following`, plus `--sandbox` mode for safe
testing against the local playground mock.

## Tooling
All work goes through the checked-in CLI. Never hand-roll HTTP calls.

```sh
python3 ~/workspace/skills/x/bin/x.py auth login --client-id <id>  # one-time
python3 ~/workspace/skills/x/bin/x.py auth status [--json]
python3 ~/workspace/skills/x/bin/x.py me [--json]
python3 ~/workspace/skills/x/bin/x.py post --text "..." [--dry-run] [--json]
python3 ~/workspace/skills/x/bin/x.py post --text - < post.txt   # read text from stdin
python3 ~/workspace/skills/x/bin/x.py post --text "..." --reply-to <id>   # thread reply
python3 ~/workspace/skills/x/bin/x.py delete --id <post_id>
python3 ~/workspace/skills/x/bin/x.py timeline [--count 10] [--since-id <id>] [--json]
python3 ~/workspace/skills/x/bin/x.py mentions [--count 10] [--json]
python3 ~/workspace/skills/x/bin/x.py user-tweets --user <handle-or-id> [--json]
python3 ~/workspace/skills/x/bin/x.py search --query "..." [--count 10] [--json]
python3 ~/workspace/skills/x/bin/x.py user --handle <handle> [--json]
python3 ~/workspace/skills/x/bin/x.py like --id <post_id>        # unlike/repost/unrepost/bookmark/unbookmark similar
python3 ~/workspace/skills/x/bin/x.py follow --user <handle>    # unfollow similar
python3 ~/workspace/skills/x/bin/x.py followers --user <handle> [--json]
python3 ~/workspace/skills/x/bin/x.py auth logout
```

**Sandbox mode** — pass `--sandbox` before the subcommand to run against the
local X API playground mock instead of the real API:
```sh
python3 ~/workspace/skills/x/bin/x.py --sandbox auth status
python3 ~/workspace/skills/x/bin/x.py --sandbox post --text "test" --dry-run
python3 ~/workspace/skills/x/bin/x.py --sandbox timeline
```
No login is needed and nothing is published for real, so every command
is safe to exercise. The mock needs to be running (`~/workspace/x-playground/RUN.md`).
Two known mock gaps vs real X: it rejects `expansions=author_id` (the skill
drops it automatically in sandbox mode) and it has no
`GET /2/users/by/username/:username` (use numeric user IDs in sandbox mode).

## Auth
One-time interactive login; tokens then auto-refresh forever.

```sh
python3 ~/workspace/skills/x/bin/x.py auth login --client-id <id>
```
The CLI prints an X authorize URL (PKCE, S256). The user approves in their
browser, pastes back the redirected URL/code, and (if their app is a
confidential client) their client secret. The CLI exchanges the code and
stores access + refresh tokens and the client credentials in
`~/.x-skill/tokens.json` (mode 0600; never printed, logged, or committed).
Every API call uses the access token and refreshes it transparently when it
expires (~2h), including one automatic retry on an unexpected 401. `auth logout`
deletes the file.

Setup the user does once (console.x.com): create a Project + App, enable
OAuth 2.0 with read+write permissions, register the callback
`http://localhost:3000/callback`, copy the Client ID (+ Secret), fund $10–20
in prepaid credits.

Why not the platform's OAuth connector: X mandates PKCE and the platform's
`oauth2_code` flow does not do PKCE (verified in the tool schema), so the
platform flow cannot complete against X. The skill therefore manages its own
tokens, twurl-style. There is no `custom.x` connector for this skill.

Authenticated requests go only to `api.x.com` (or the sandbox mock).

**Cost model (pay-per-use, Oct 2026 — re-verify in console.x.com):**
- Post: $0.015. **Post containing a URL: $0.20 (13x).** `--dry-run` shows it.
- Reads bill per resource: post $0.005, own data $0.001, user $0.010,
  follow $0.010, like $0.001. Same-resource same-day re-reads are deduped.
- Reads default to 10 items; use `--since-id` for incremental checks.

**Error handling:**
- **HTTP 401** means the token is revoked or the refresh failed. The skill
  already tried one automatic refresh. Tell the user to re-run `auth login`.
- **HTTP 403 with "duplicate"** means X rejected identical text to a recent
  post — change the wording, don't retry verbatim.
- **HTTP 429** means rate-limited: report X's reset time, do not auto-retry
  (every retry bills).

## Operating Rules
1. **Every post needs the user's explicit approval of the exact text in chat
   before sending.** Draft the text, show it verbatim, run with `--dry-run`
   to show payload + cost, and only run `post` (without `--dry-run`) after
   he says go. The skill never auto-posts; test posts are never published.
2. Posts cap at 280 chars (the CLI enforces this).
3. Reads are cheap but not free: default `--count` is 10; prefer `--since-id`
   over re-polling; never loop reads on a timer without the user asking.
4. On success `post` prints the tweet URL — include it when confirming to the user.
5. If a call fails, surface the CLI's error message; it already distinguishes
   expired tokens (401), duplicates (403), scopes (403), and rate limits (429).
6. API mechanics (endpoints, PKCE details, scopes, rate limits) live in
   `references/api-notes.md` — read it before changing request shapes.
7. X enforces its automation/spam policy: keep bot-like bursts (mass follows,
   likes, posts) out of routine use; paced, human-scale actions only.

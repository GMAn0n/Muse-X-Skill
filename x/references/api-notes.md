# X API v2 notes (researched 2026-10-08; re-verify pricing in console.x.com)

Base: `https://api.x.com/2`. Auth: `Authorization: Bearer <token>` (user
context; the skill manages its own OAuth 2.0 tokens in `~/.x-skill/tokens.json`
and refreshes them automatically). v1.1 is fully deprecated — use `api.x.com/2` only.

## Pricing (pay-per-use; new developers have no free tier)

| Operation | Price |
|---|---|
| Post created | $0.015 / request |
| **Post containing a URL** | **$0.20 / request (13x)** |
| Post read (per post returned) | $0.005 |
| Owned read — your own data (me) | $0.001 |
| User read (per user) | $0.010 |
| Follows read (per resource) | $0.010 |
| Like read | $0.001 |

Billing is per resource returned, not per request; the same resource
re-requested within the same UTC day is deduplicated (charged once).
~2M post-read cap per monthly cycle. Fund $10–20 with auto-recharge and a
spending limit.

## Auth — OAuth 2.0 Authorization Code + PKCE (mandatory)

- Authorize: `https://x.com/i/oauth2/authorize`
- Token: `POST https://api.x.com/2/oauth2/token`
- PKCE S256: `code_verifier` = 43–128 chars from `[A-Za-z0-9-._~]`
  (`secrets.token_urlsafe(64)`); `code_challenge` =
  base64url(sha256(verifier)) with padding stripped.
- Authorize params: `response_type=code`, `client_id`, `redirect_uri`
  (`http://localhost:3000/callback` — must match the X app's registered
  callback exactly), `scope` (space-separated), `state`,
  `code_challenge`, `code_challenge_method=S256`.
- v1 scopes: `tweet.read tweet.write users.read offline.access like.read
  like.write follows.read follows.write bookmark.read bookmark.write`
- Public-client exchange (no client secret): form-encoded POST with
  `grant_type=authorization_code`, `code`, `redirect_uri`, `client_id`,
  `code_verifier`. Confidential clients must use HTTP Basic instead —
  the skill does not implement that path (use a public app type or the
  platform OAuth flow).
- Response: `access_token` (~2h), `refresh_token` (requires
  `offline.access`; rotates on use), `expires_in`, `scope`.
- **Bearer (app-only) tokens can never post as a user.** Every write needs
  a user-context token.

## Endpoints (v1)

- `POST /2/tweets` — body `{"text": ...}`, optional
  `reply.in_reply_to_tweet_id` (threads = chained replies),
  `quote_tweet_id`. 280 chars. Duplicate text → hard 403.
- `DELETE /2/tweets/:id`
- `GET /2/tweets/:id`, `GET /2/tweets?ids=...` (batch ≤100)
- `GET /2/tweets/search/recent` — 7-day window, `query` ≤512 chars,
  `max_results` ≤100. Full-archive `/search/all` is tier-gated (uncertain
  for pay-per-use) — not in v1.
- `GET /2/users/:id/timelines/reverse_chronological` — user-context only,
  `:id` must be the token owner's own ID.
- `GET /2/users/:id/mentions`, `GET /2/users/:id/tweets`
- `GET /2/users/me`, `GET /2/users/:id`, `GET /2/users/by/username/:u`
- Likes: `POST|DELETE /2/users/:id/likes` (body `{"tweet_id"}`),
  `GET /2/tweets/:id/liking_users`, `GET /2/users/:id/liked_tweets`
- Reposts: `POST|DELETE /2/users/:id/retweets` (body `{"tweet_id"}`),
  `GET /2/tweets/:id/retweeted_by`, `GET /2/tweets/:id/quote_tweets`
- Bookmarks: `POST|DELETE /2/users/:id/bookmarks` (body `{"tweet_id"}`)
- Follows: `POST|DELETE /2/users/:id/following` (body
  `{"target_user_id"}`), `GET /2/users/:id/followers|following`
- Common query params: `tweet.fields=created_at,public_metrics,author_id`,
  `expansions=author_id`, `user.fields=username,name`,
  `max_results`, `since_id`, `pagination_token`.
- Error shape: `{"errors":[{"message":...}], "title":..., "detail":...}`.

## Rate limits (per 15 min unless noted; 429 → wait for x-rate-limit-reset)

`POST /2/tweets`: 100/user + 10,000/24h/app. `DELETE /2/tweets/:id`:
50/user. `search/recent`: 300/user. `mentions`: 300/user.
`reverse_chronological`: 180/user. `users/:id/tweets`: 900/user.
The skill never auto-retries 429s (every retry bills).

## Gotchas

- No LinkedIn-style silent truncation: what you send is what renders.
- Duplicate post text → HTTP 403, must reword.
- X actively enforces automation/spam policy — keep usage human-scale.
- DMs: events retained 30 days only; no list-conversations endpoint (v2).
- Media upload is chunked (`POST /2/media/upload` INIT→APPEND→FINALIZE→
  STATUS), then attach `media_ids` (v2, not implemented).
- Lists, Spaces, filtered stream (Pro/Enterprise-gated): v2 roadmap.

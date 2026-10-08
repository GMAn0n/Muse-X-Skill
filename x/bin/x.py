#!/usr/bin/env python3
"""X (Twitter) CLI.

Subcommands:
  auth login      One-shot interactive login (PKCE). Stores tokens; they
                  auto-refresh, so one login lasts indefinitely.
  auth logout     Delete stored tokens.
  auth status     Show login state.
  me              Authenticated user profile.
  post            Publish a post. Requires the user's explicit approval of the exact
                  text in chat BEFORE running. --dry-run previews payload + cost.
  delete          Delete one of your posts by ID.
  timeline        Your home timeline (recent posts from accounts you follow).
  mentions        Posts mentioning you.
  user-tweets     Recent posts from a user (--user handle or ID).
  search          Recent search (7-day window).
  user            Look up a user by handle.
  like / unlike   Like or unlike a post by ID.
  repost / unrepost
  bookmark / unbookmark
  follow / unfollow
  followers / following

Auth model: OAuth 2.0 with PKCE. `auth login` walks through the browser
approval once, then stores access + refresh tokens and the client credentials
in ~/.x-skill/tokens.json (mode 0600). API calls use the access token and
refresh it transparently when it expires (~2h), so the login never goes stale.
Tokens are never printed, logged, or committed. `auth logout` deletes them.

Sandbox: pass --sandbox (before the subcommand) to run against the local X
API playground mock at http://localhost:8080 instead of the real API.
No login is needed and nothing is published for real, so it is safe for
testing every command. The mock has two gaps vs real X (documented in
SKILL.md): it rejects expansions=author_id and has no /2/users/by/username/.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.x.com"
AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
# the user registers exactly this in the X app's OAuth 2.0 settings.
REDIRECT_URI = "http://localhost:3000/callback"
# v1 scope set: posting, reading, engagement. No DMs / media / lists in v1.
SCOPES = (
    "tweet.read tweet.write users.read offline.access "
    "like.read like.write follows.read follows.write "
    "bookmark.read bookmark.write"
)
TWEET_LIMIT = 280

# X pay-per-use costs (USD, Oct 2026 — re-verify in console.x.com).
COST_POST = 0.015
COST_POST_WITH_URL = 0.20
COST_POST_READ = 0.005
COST_OWN_READ = 0.001
COST_USER_READ = 0.010
COST_FOLLOW_READ = 0.010
COST_LIKE_READ = 0.001

# Token storage: the skill manages its own OAuth 2.0 tokens (twurl-style).
# X access tokens live ~2h; the skill refreshes them automatically using the
# stored refresh token + client credentials, so one login lasts indefinitely.
# File is 0600, directory 0700. Never printed, logged, or committed.
TOKEN_DIR = os.path.expanduser("~/.x-skill")
TOKEN_FILE = os.path.join(TOKEN_DIR, "tokens.json")

# Sandbox mode (--sandbox): talk to the local X API playground mock instead of
# the real api.x.com. No login needed; requests carry a dummy Bearer token
# plus X-Auth-Method: oauth2user to simulate OAuth 2.0 user context.
# Two known mock gaps (real X supports both): the mock rejects
# expansions=author_id (dropped in sandbox mode) and does not implement
# GET /2/users/by/username/:username (use numeric user IDs in sandbox mode).
SANDBOX = False
SANDBOX_BASE = "http://localhost:8080"
SANDBOX_TOKEN = "test_token"


class XError(RuntimeError):
    """User-facing error; never includes credential material."""


def _friendly_error(status, body, path, headers=None):
    snippet = ""
    try:
        data = json.loads(body or "")
        errs = data.get("errors") or []
        if errs:
            snippet = "; ".join(
                str(e.get("message") or e.get("detail") or e) for e in errs
            )[:500]
        else:
            # v2 shape uses detail/title; OAuth token errors use
            # error/error_description.
            snippet = str(data.get("detail") or data.get("title") or
                          data.get("error_description") or
                          data.get("error") or "")[:500]
    except Exception:
        snippet = (body or "").strip()[:500]
    if status == 401:
        return (
            "X returned HTTP 401: the access token is expired, revoked, or invalid.\n"
            "The skill already tried one automatic refresh. If this persists, "
            "the refresh token is dead — re-run: x.py auth login\n"
            f"X said: {snippet}"
        )
    if status == 403:
        low = snippet.lower()
        if "duplicate" in low:
            return (
                "X refused the post as a duplicate (HTTP 403): X hard-errors on "
                "identical text to a recent post. Change the wording and retry.\n"
                f"X said: {snippet}"
            )
        return (
            "X returned HTTP 403: forbidden for this call.\n"
            "Likely cause: missing scope on the X app (needs tweet.write for "
            "posting, tweet.read for reads, etc.) or X's automation policy "
            "flagged the action. Check the app's permissions in console.x.com.\n"
            f"X said: {snippet}"
        )
    if status == 429:
        reset = (headers or {}).get("x-rate-limit-reset", "?")
        return (
            "X rate-limited the call (HTTP 429). The skill does not auto-retry "
            f"(every retry bills). Limit resets at unix time {reset}; wait and "
            "retry once, or reduce polling frequency.\n"
            f"X said: {snippet}"
        )
    return f"X returned HTTP {status} for {path}. X said: {snippet}"


def _load_tokens():
    """Stored OAuth tokens, or None if not logged in."""
    try:
        with open(TOKEN_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_tokens(tok):
    os.makedirs(TOKEN_DIR, mode=0o700, exist_ok=True)
    tmp = TOKEN_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(tok, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, TOKEN_FILE)


def _token_request(form, client_id, client_secret, timeout=30):
    """POST a form to the OAuth token endpoint. Returns the parsed JSON."""
    headers = {"Content-Type": "application/x-www-form-urlencoded",
               "Accept": "application/json"}
    if client_secret:
        basic = base64.b64encode(
            f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {basic}"
    data = urllib.parse.urlencode(form).encode("ascii")
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        raise XError(_friendly_error(e.code, err_body, "/oauth2/token"))
    except urllib.error.URLError as e:
        raise XError(f"Network error calling X token endpoint: {e.reason}")


def refresh_access_token(tok, timeout=30):
    """Mint a new access token from the stored refresh token. X rotates
    refresh tokens, so the file is updated in place. Returns updated dict."""
    if not tok.get("refresh_token"):
        raise XError("No refresh token stored. Re-run: x.py auth login")
    new = _token_request(
        {"grant_type": "refresh_token",
         "refresh_token": tok["refresh_token"],
         "client_id": tok["client_id"]},
        tok["client_id"], tok.get("client_secret"), timeout)
    if "access_token" not in new:
        raise XError("Token refresh failed (refresh token may be revoked). "
                     "Re-run: x.py auth login")
    tok["access_token"] = new["access_token"]
    tok["refresh_token"] = new.get("refresh_token", tok["refresh_token"])
    tok["expires_at"] = time.time() + int(new.get("expires_in", 7200)) - 60
    tok["scope"] = new.get("scope", tok.get("scope"))
    _save_tokens(tok)
    return tok


def _access_token():
    """A valid access token, refreshing transparently when expired."""
    tok = _load_tokens()
    if not tok or not tok.get("access_token"):
        raise XError("Not logged in. Run: x.py auth login --client-id <id>")
    if tok.get("expires_at", 0) <= time.time():
        tok = refresh_access_token(tok)
    return tok["access_token"]


def api(method, path, body=None, params=None, timeout=30, _retried=False):
    """Authenticated X API v2 call. Returns (status, json, headers)."""
    url = (SANDBOX_BASE if SANDBOX else API_BASE) + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if SANDBOX:
        req.add_header("Authorization", f"Bearer {SANDBOX_TOKEN}")
        req.add_header("X-Auth-Method", "oauth2user")
    else:
        req.add_header("Authorization", f"Bearer {_access_token()}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            try:
                payload = json.load(resp)
            except Exception:
                payload = {}
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, payload, headers
    except urllib.error.HTTPError as e:
        if e.code == 401 and not SANDBOX and not _retried:
            # Token may have expired early; refresh once and retry.
            tok = _load_tokens()
            if tok and tok.get("refresh_token"):
                try:
                    refresh_access_token(tok)
                except XError:
                    pass
                else:
                    return api(method, path, body, params, timeout, True)
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        raise XError(_friendly_error(
            e.code, err_body, path, {k.lower(): v for k, v in (e.headers or {}).items()}))
    except urllib.error.URLError as e:
        raise XError(f"Network error calling X {path}: {e.reason}")


# --------------------------------------------------------------------------
# Cost estimation (upper bounds; X dedups same-resource same-day re-reads)
# --------------------------------------------------------------------------

def _has_url(text):
    return "http://" in text or "https://" in text


def estimate_post_cost(text):
    return COST_POST_WITH_URL if _has_url(text) else COST_POST


def estimate_read_cost(count, per_item):
    return round(count * per_item, 4)


# --------------------------------------------------------------------------
# PKCE auth ceremony (manual fallback / diagnostic)
# --------------------------------------------------------------------------

def pkce_verifier():
    """43-128 char high-entropy verifier (RFC 7636 unreserved alphabet)."""
    return secrets.token_urlsafe(64)[:86]


def pkce_challenge(verifier):
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build_authorize_url(client_id, redirect_uri=REDIRECT_URI, scopes=SCOPES):
    verifier = pkce_verifier()
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": secrets.token_urlsafe(16),
        "code_challenge": pkce_challenge(verifier),
        "code_challenge_method": "S256",
    }
    return (AUTHORIZE_URL + "?" + urllib.parse.urlencode(params), verifier,
            params["state"])


def exchange_code(code, verifier, client_id, client_secret=None,
                  redirect_uri=REDIRECT_URI, timeout=30):
    """Exchange an authorization code for tokens. Returns the token dict.

    Raises XError on failure. The caller stores the result via _save_tokens;
    tokens are never printed.
    """
    return _token_request(
        {"grant_type": "authorization_code",
         "code": code,
         "redirect_uri": redirect_uri,
         "client_id": client_id,
         "code_verifier": verifier},
        client_id, client_secret, timeout)


def cmd_auth_login(args):
    """One-shot interactive PKCE login. Prints the authorize URL, prompts for
    the pasted code, exchanges it, and stores tokens (0600) for good."""
    if SANDBOX:
        raise XError("--sandbox needs no login; the mock accepts any token.")
    url, verifier, state = build_authorize_url(args.client_id)
    print("1. Open this URL in your browser and approve the app:")
    print()
    print(url)
    print()
    print("2. X redirects to http://localhost:3000/callback?code=... (the page")
    print("   will not load — that's expected). Copy the full URL or just the code.")
    print()
    code = input("Paste the redirected URL or code: ").strip()
    if "code=" in code:
        code = urllib.parse.parse_qs(
            urllib.parse.urlsplit(code).query).get("code", [code])[0]
    if not code:
        raise XError("No code entered; login cancelled.")
    secret = getpass.getpass(
        "Client secret (from your X app settings; Enter to skip): ").strip()
    tokens = exchange_code(code, verifier, args.client_id, secret or None)
    if "access_token" not in tokens:
        raise XError("Login failed: X did not return an access token. "
                     "The code may have expired — run auth login again.")
    tok = {
        "client_id": args.client_id,
        "client_secret": secret or None,
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token"),
        "expires_at": time.time() + int(tokens.get("expires_in", 7200)) - 60,
        "scope": tokens.get("scope"),
    }
    if not tok["refresh_token"]:
        print("WARNING: X issued no refresh token (is offline.access granted?).")
        print("The login will last ~2 hours; re-run auth login when it expires.")
    _save_tokens(tok)
    # Verify against the live API and cache the identity.
    status, data, _ = api("GET", "/2/users/me",
                          params={"user.fields": "username,name"})
    me = data.get("data", {})
    tok["username"] = me.get("username")
    tok["user_id"] = me.get("id")
    _save_tokens(tok)
    print()
    print(f"Logged in as @{tok['username']} (id {tok['user_id']}).")
    print(f"Tokens stored in {TOKEN_FILE} (mode 0600); they refresh automatically.")


def cmd_auth_logout(args):
    try:
        os.remove(TOKEN_FILE)
    except OSError:
        print("Not logged in (no stored tokens).")
        return
    print("Logged out; stored tokens deleted.")


def cmd_auth_status(args):
    if SANDBOX:
        status, data, _ = api("GET", "/2/users/me",
                              params={"user.fields": "username,name"})
        me = data.get("data", {})
        out = {"mode": "sandbox", "live": True,
               "username": me.get("username"), "user_id": me.get("id")}
        if args.json:
            print(json.dumps(out, indent=2))
        else:
            print(f"Sandbox mode: playground mock is live "
                  f"(@{out['username']}, id {out['user_id']}). "
                  f"No credential needed.")
        return
    tok = _load_tokens()
    if not tok or not tok.get("access_token"):
        print("Not logged in. Run: x.py auth login --client-id <id>")
        return
    exp_in = int(tok.get("expires_at", 0) - time.time())
    out = {
        "logged_in": True,
        "username": tok.get("username"),
        "user_id": tok.get("user_id"),
        "access_token_expires_in_s": max(0, exp_in),
        "auto_refresh": bool(tok.get("refresh_token")),
        "scope": tok.get("scope"),
    }
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"Logged in as @{out['username']} (id {out['user_id']}).")
        print(f"  access token expires in ~{max(0, exp_in) // 60} min; "
              f"auto-refresh {'on' if out['auto_refresh'] else 'OFF'}")
        print(f"  scope: {out['scope']}")


# --------------------------------------------------------------------------
# API commands
# --------------------------------------------------------------------------

TWEET_FIELDS = "created_at,public_metrics,author_id"
USER_FIELDS = "username,name"
EXPANSIONS = "author_id"


def _users_by_id(users):
    return {u.get("id"): u for u in (users or []) if u.get("id")}


def _fmt_tweet(t, user_map):
    author = user_map.get(t.get("author_id") or "", {})
    handle = author.get("username", "?")
    m = t.get("public_metrics") or {}
    text = (t.get("text") or "").replace("\n", " ")
    if len(text) > 160:
        text = text[:157] + "..."
    return (f"@{handle} [{t.get('id')}] {t.get('created_at', '')}\n"
            f"  {text}\n"
            f"  likes={m.get('like_count', '?')} reposts={m.get('retweet_count', '?')} "
            f"replies={m.get('reply_count', '?')} quotes={m.get('quote_count', '?')}")


def _print_tweets(data, args, cost_each):
    tweets = data.get("data") or []
    user_map = _users_by_id((data.get("includes") or {}).get("users"))
    meta = data.get("meta") or {}
    if args.json:
        print(json.dumps({
            "tweets": tweets,
            "includes": data.get("includes", {}),
            "meta": meta,
            "est_cost_usd": estimate_read_cost(len(tweets), cost_each),
        }, indent=2))
        return
    if not tweets:
        print("No posts returned.")
        return
    for t in tweets:
        print(_fmt_tweet(t, user_map))
        print()
    print(f"({len(tweets)} posts, est. cost "
          f"${estimate_read_cost(len(tweets), cost_each):.4f})")


def _tweet_params(count, since_id=None):
    p = {"max_results": max(10, min(100, count)),
         "tweet.fields": TWEET_FIELDS,
         "user.fields": USER_FIELDS}
    if not SANDBOX:
        # The playground mock's spec only allows pinned_tweet_id here;
        # real X accepts author_id, so keep it for production calls.
        p["expansions"] = EXPANSIONS
    if since_id:
        p["since_id"] = since_id
    return p


def cmd_me(args):
    status, data, _ = api("GET", "/2/users/me",
                          params={"user.fields": "username,name,description,public_metrics"})
    me = data.get("data", {})
    if args.json:
        print(json.dumps({"me": me,
                          "est_cost_usd": COST_OWN_READ}, indent=2))
        return
    pm = me.get("public_metrics") or {}
    print(f"@{me.get('username')} — {me.get('name')} (id {me.get('id')})")
    print(f"  bio: {(me.get('description') or '')[:120]}")
    print(f"  followers={pm.get('followers_count', '?')} "
          f"following={pm.get('following_count', '?')} "
          f"posts={pm.get('tweet_count', '?')}")


def cmd_post(args):
    text = args.text
    if text == "-":
        text = sys.stdin.read()
    text = (text or "").strip()
    if not text:
        raise XError('Post text is empty. Pass --text "..." or pipe it with --text -')
    if len(text) > TWEET_LIMIT:
        raise XError(
            f"Post text is {len(text)} chars; X's limit is {TWEET_LIMIT}.")
    body = {"text": text}
    if args.reply_to and args.quote_tweet_id:
        raise XError("Use --reply-to or --quote-tweet-id, not both.")
    if args.reply_to:
        body["reply"] = {"in_reply_to_tweet_id": args.reply_to}
    if args.quote_tweet_id:
        body["quote_tweet_id"] = args.quote_tweet_id
    cost = estimate_post_cost(text)
    if args.dry_run:
        preview = {
            "method": "POST",
            "url": API_BASE + "/2/tweets",
            "body": body,
            "char_count": len(text),
            "contains_url": _has_url(text),
            "est_cost_usd": cost,
            "cost_note": ("$0.20 because the text contains a URL (13x). "
                          if _has_url(text) else "Standard $0.015 text post."),
        }
        print(json.dumps(preview, indent=2))
        print("\nDRY RUN - nothing was published.")
        return
    status, data, _ = api("POST", "/2/tweets", body=body)
    tid = (data.get("data") or {}).get("id")
    out = {"status": status, "tweet_id": tid,
           "url": f"https://x.com/i/status/{tid}" if tid else None,
           "est_cost_usd": cost}
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"Published (HTTP {status}).")
        if tid:
            print(f"URL: {out['url']}")


def cmd_delete(args):
    status, data, _ = api("DELETE", f"/2/tweets/{args.id}")
    deleted = (data.get("data") or {}).get("deleted")
    out = {"status": status, "deleted": deleted, "tweet_id": args.id}
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"Deleted {args.id} (HTTP {status}, deleted={deleted}).")


def _me_id():
    _, data, _ = api("GET", "/2/users/me")
    mid = (data.get("data") or {}).get("id")
    if not mid:
        raise XError("Could not resolve your user ID from /2/users/me.")
    return mid


def cmd_timeline(args):
    mid = _me_id()
    _, data, _ = api("GET", f"/2/users/{mid}/timelines/reverse_chronological",
                     params=_tweet_params(args.count, args.since_id))
    _print_tweets(data, args, COST_POST_READ)


def cmd_mentions(args):
    mid = _me_id()
    _, data, _ = api("GET", f"/2/users/{mid}/mentions",
                     params=_tweet_params(args.count, args.since_id))
    _print_tweets(data, args, COST_POST_READ)


def resolve_user(user):
    """Handle or numeric ID -> (id, username)."""
    user = user.strip().lstrip("@")
    if user.isdigit():
        _, data, _ = api("GET", f"/2/users/{user}",
                         params={"user.fields": USER_FIELDS})
    else:
        _, data, _ = api("GET", f"/2/users/by/username/{user}",
                         params={"user.fields": USER_FIELDS})
    d = data.get("data") or {}
    if not d.get("id"):
        raise XError(f"Could not resolve user '{user}'.")
    return d["id"], d.get("username", user)


def cmd_user_tweets(args):
    uid, handle = resolve_user(args.user)
    _, data, _ = api("GET", f"/2/users/{uid}/tweets",
                     params=_tweet_params(args.count, args.since_id))
    _print_tweets(data, args, COST_POST_READ)


def cmd_search(args):
    if len(args.query) > 512:
        raise XError("X recent-search queries are capped at 512 chars.")
    _, data, _ = api("GET", "/2/tweets/search/recent",
                     params={**_tweet_params(args.count, args.since_id),
                             "query": args.query})
    _print_tweets(data, args, COST_POST_READ)


def cmd_user(args):
    uid, handle = resolve_user(args.handle)
    _, data, _ = api("GET", f"/2/users/{uid}",
                     params={"user.fields": "username,name,description,public_metrics"})
    u = data.get("data", {})
    if args.json:
        print(json.dumps({"user": u, "est_cost_usd": COST_USER_READ}, indent=2))
        return
    pm = u.get("public_metrics") or {}
    print(f"@{u.get('username')} — {u.get('name')} (id {u.get('id')})")
    print(f"  bio: {(u.get('description') or '')[:160]}")
    print(f"  followers={pm.get('followers_count', '?')} "
          f"following={pm.get('following_count', '?')}")


def _engagement(method, action_path, args, what):
    mid = _me_id()
    if method == "POST":
        _, data, _ = api("POST", f"/2/users/{mid}/{action_path}",
                         body={"tweet_id": args.id})
    else:
        _, data, _ = api("DELETE", f"/2/users/{mid}/{action_path}/{args.id}")
    out = {"status": "ok", what: data.get("data"), "tweet_id": args.id}
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"{what} {args.id}: {data.get('data')}")


def cmd_like(a): _engagement("POST", "likes", a, "liked")


def cmd_unlike(a): _engagement("DELETE", "likes", a, "unliked")


def cmd_repost(a): _engagement("POST", "retweets", a, "reposted")


def cmd_unrepost(a): _engagement("DELETE", "retweets", a, "unreposted")


def cmd_bookmark(a): _engagement("POST", "bookmarks", a, "bookmarked")


def cmd_unbookmark(a): _engagement("DELETE", "bookmarks", a, "unbookmarked")


def cmd_follow(args):
    mid = _me_id()
    uid, handle = resolve_user(args.user)
    _, data, _ = api("POST", f"/2/users/{mid}/following",
                     body={"target_user_id": uid})
    d = data.get("data") or {}
    out = {"status": "ok", "following": d.get("following"),
           "target": handle}
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"Following @{handle} (following={d.get('following')}).")


def cmd_unfollow(args):
    mid = _me_id()
    uid, handle = resolve_user(args.user)
    _, data, _ = api("DELETE", f"/2/users/{mid}/following/{uid}")
    d = data.get("data") or {}
    out = {"status": "ok", "following": d.get("following"), "target": handle}
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"Unfollowed @{handle} (following={d.get('following')}).")


def _print_users(data, args):
    users = data.get("data") or []
    meta = data.get("meta") or {}
    if args.json:
        print(json.dumps({"users": users, "meta": meta,
                          "est_cost_usd": estimate_read_cost(
                              len(users), COST_FOLLOW_READ)}, indent=2))
        return
    for u in users:
        print(f"@{u.get('username')} — {u.get('name')} (id {u.get('id')})")
    print(f"({len(users)} users, est. cost "
          f"${estimate_read_cost(len(users), COST_FOLLOW_READ):.4f})")


def cmd_followers(args):
    uid, _ = resolve_user(args.user)
    _, data, _ = api("GET", f"/2/users/{uid}/followers",
                     params={"max_results": max(5, min(100, args.count)),
                             "user.fields": USER_FIELDS})
    _print_users(data, args)


def cmd_following(args):
    uid, _ = resolve_user(args.user)
    _, data, _ = api("GET", f"/2/users/{uid}/following",
                     params={"max_results": max(5, min(100, args.count)),
                             "user.fields": USER_FIELDS})
    _print_users(data, args)


def _add_common(sp):
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--count", type=int, default=10,
                    help="Results to fetch (default 10; reads bill per item).")


def _add_since(sp):
    sp.add_argument("--since-id",
                    help="Only items newer than this post ID (saves money).")


def _add_id(sp):
    sp.add_argument("--id", required=True, help="Post ID.")


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="x.py",
        description="X (Twitter) CLI (OAuth 2.0, self-managed tokens).",
    )
    p.add_argument("--sandbox", action="store_true",
                   help="Talk to the local X API playground mock "
                        "(http://localhost:8080) instead of the real API. "
                        "No credential needed; nothing is published for real.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("auth", help="Login / logout / status.")
    asub = sp.add_subparsers(dest="auth_cmd", required=True)
    s = asub.add_parser("login", help="One-shot interactive login (PKCE). "
                                      "Stores tokens; they auto-refresh.")
    s.add_argument("--client-id", required=True, help="X app client ID (public).")
    s.set_defaults(func=cmd_auth_login)
    s = asub.add_parser("logout", help="Delete stored tokens.")
    s.set_defaults(func=cmd_auth_logout)
    s = asub.add_parser("status", help="Show login state.")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_auth_status)

    sp = sub.add_parser("me", help="Your profile.")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_me)

    sp = sub.add_parser("post", help="Publish a post (needs the user's approval first).")
    sp.add_argument("--text", required=True,
                    help='Post text, or "-" to read from stdin.')
    sp.add_argument("--reply-to", help="Post ID to reply to (threads).")
    sp.add_argument("--quote-tweet-id", help="Post ID to quote.")
    sp.add_argument("--dry-run", action="store_true",
                    help="Print payload + cost estimate without publishing.")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_post)

    sp = sub.add_parser("delete", help="Delete one of your posts by ID.")
    _add_id(sp); sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_delete)

    for name, fn, help_ in [
        ("timeline", cmd_timeline, "Your home timeline."),
        ("mentions", cmd_mentions, "Posts mentioning you."),
    ]:
        sp = sub.add_parser(name, help=help_)
        _add_common(sp); _add_since(sp)
        sp.set_defaults(func=fn)

    sp = sub.add_parser("user-tweets", help="Recent posts from a user.")
    sp.add_argument("--user", required=True, help="Handle or user ID.")
    _add_common(sp); _add_since(sp)
    sp.set_defaults(func=cmd_user_tweets)

    sp = sub.add_parser("search", help="Recent search (7-day window).")
    sp.add_argument("--query", required=True, help="Search query (<=512 chars).")
    _add_common(sp); _add_since(sp)
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("user", help="Look up a user by handle.")
    sp.add_argument("--handle", required=True)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_user)

    for name, fn in [("like", cmd_like), ("unlike", cmd_unlike),
                     ("repost", cmd_repost), ("unrepost", cmd_unrepost),
                     ("bookmark", cmd_bookmark), ("unbookmark", cmd_unbookmark)]:
        sp = sub.add_parser(name, help=f"{name} a post by ID.")
        _add_id(sp); sp.add_argument("--json", action="store_true")
        sp.set_defaults(func=fn)

    for name, fn in [("follow", cmd_follow), ("unfollow", cmd_unfollow)]:
        sp = sub.add_parser(name, help=f"{name} a user.")
        sp.add_argument("--user", required=True, help="Handle or user ID.")
        sp.add_argument("--json", action="store_true")
        sp.set_defaults(func=fn)

    for name, fn in [("followers", cmd_followers), ("following", cmd_following)]:
        sp = sub.add_parser(name, help=f"{name} list for a user.")
        sp.add_argument("--user", required=True, help="Handle or user ID.")
        sp.add_argument("--count", type=int, default=10)
        sp.add_argument("--json", action="store_true")
        sp.set_defaults(func=fn)

    args = p.parse_args(argv)
    global SANDBOX
    if args.sandbox:
        SANDBOX = True
    try:
        args.func(args)
    except XError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

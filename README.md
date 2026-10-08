# X Skill for Muse

Operate your X (Twitter) account from Muse: publish posts, read your home
timeline and mentions, search recent posts, look up users, and manage likes,
reposts, bookmarks, and follows — all through the official X API v2.

## Highlights

- **One-time login, then it just works.** `auth login` walks through X's OAuth
  2.0 (PKCE) once in your browser. Tokens are stored locally (`~/.x-skill/`,
  mode 0600) and refresh automatically — no re-login, ever.
- **Full v1 command set:** `me`, `post` (with `--dry-run` cost preview,
  replies, quotes), `delete`, `timeline`, `mentions`, `user-tweets`,
  `search`, `user`, `like`/`unlike`, `repost`/`unrepost`,
  `bookmark`/`unbookmark`, `follow`/`unfollow`, `followers`/`following`.
- **Cost-aware.** X bills pay-per-use; every `--dry-run` shows the exact
  estimated cost before anything is spent. Reads default to 10 items.
- **Sandbox mode.** `--sandbox` runs every command against a local mock of
  the X API, so you can test safely with zero spend and zero credentials.
- **Approval-gated posting.** The skill never posts without your explicit
  approval of the exact text.

## Requirements

- Python 3.8+
- An X developer app (free to create; API usage is pay-per-use with prepaid
  credits — a few posts a day costs ~$1–2/month). See SETUP.md.

## Quick start

```sh
# 1. Copy the skill into your Muse workspace
cp -r x ~/workspace/skills/

# 2. One-time login (opens X in your browser)
python3 ~/workspace/skills/x/bin/x.py auth login --client-id <your-client-id>

# 3. Post (shows cost first, then asks for your approval in chat)
python3 ~/workspace/skills/x/bin/x.py post --text "Hello, world" --dry-run

# 4. Try the sandbox (no login, no spend, nothing published)
python3 ~/workspace/skills/x/bin/x.py --sandbox timeline
```

See [SETUP.md](SETUP.md) for the full walkthrough, including creating the X
app, funding credits, and troubleshooting.

## Costs (X pay-per-use, Oct 2026 — re-verify in console.x.com)

| Operation | Price |
|---|---|
| Text post | $0.015 |
| Post containing a URL | **$0.20** (13x) |
| Post read | $0.005 |
| Own data read | $0.001 |

## License

MIT — see LICENSE.

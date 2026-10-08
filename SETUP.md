# Setup Guide — X Skill for Muse

## 1. Create an X developer app (~5 minutes)

1. Go to [console.x.com](https://console.x.com) and sign in.
2. Create a **Project** (required — API calls fail without one), then create an
   **App** inside it.
3. In the app's **User authentication settings**:
   - Enable **OAuth 2.0**.
   - Set **Type of App** to *Web App*.
   - Under **Callback URI / Redirect URL**, add exactly:
     `http://localhost:3000/callback`
   - Set permissions to **Read and write**.
   - Website URL can be anything (it's just shown on the authorize screen).
4. Save, then copy the **OAuth 2.0 Client ID** and **Client Secret** from the
   app's **Keys and tokens** page. Note: these are a *separate* pair from the
   API Key / API Key Secret — use the OAuth 2.0 ones.
5. Under **Billing**, add prepaid credits ($10–20 is plenty to start).
   X API is pay-per-use; see [README.md](README.md) for rates.

## 2. Install the skill

```sh
cp -r x ~/workspace/skills/
python3 ~/workspace/skills/x/bin/x.py --help
```

Requires Python 3.8+. No other dependencies.

## 3. Log in (one time)

```sh
python3 ~/workspace/skills/x/bin/x.py auth login --client-id <your-oauth2-client-id>
```

- The CLI prints an authorize URL. Open it in your browser and approve the app.
- X redirects to `http://localhost:3000/callback?code=...` — the page won't
  load; that's expected. Copy the full URL (or just the `code`) and paste it
  back into the terminal.
- Enter your **OAuth 2.0 Client Secret** when prompted (input is hidden).
- Tokens are stored in `~/.x-skill/tokens.json` (mode 0600) and refresh
  automatically. You'll never need to log in again unless you revoke access.

Verify:

```sh
python3 ~/workspace/skills/x/bin/x.py auth status
python3 ~/workspace/skills/x/bin/x.py me
```

## 4. Try the sandbox (optional, no login needed)

```sh
python3 ~/workspace/skills/x/bin/x.py --sandbox timeline
python3 ~/workspace/skills/x/bin/x.py --sandbox post --text "test" --dry-run
```

`--sandbox` routes every command to a local mock of the X API: no credentials,
no spend, nothing published. (The mock itself isn't included here; it's the
[X Dev Playground](https://github.com/xdevplatform/playground).)

## Troubleshooting

**"Something went wrong / You weren't able to give access to the App"**
on the X authorize page — the usual causes, in order:
1. The callback URL `http://localhost:3000/callback` isn't registered on the
   app (add it exactly, under User authentication settings).
2. OAuth 2.0 isn't enabled on the app.
3. You used the API Key as the client ID — X issues a *separate* OAuth 2.0
   Client ID / Secret pair. Use those.

**`403: keys and tokens from a developer App that is attached to a Project`**
— create a Project in console.x.com and attach the app to it.

**`401` on API calls** — the skill refreshes tokens automatically and retries
once. If it persists, the grant was revoked: re-run `auth login`.

**Posting costs $0.20 instead of $0.015** — posts containing URLs cost 13x.
`--dry-run` always shows the cost before publishing.

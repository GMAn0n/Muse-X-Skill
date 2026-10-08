# X Skill for Muse

> **Get it now:** [Download page](https://muse.ai/s/x-skill-for-muse-pv6c1xts59xqv) ·
> [Live site](https://gman0n.github.io/Muse-X-Skill/) ·
> [GitHub](https://github.com/GMAn0n/Muse-X-Skill)

Post, read, search, and engage on X (Twitter) from Muse — through the
official X API v2. One login, then it just works.

## Connect in 3 steps

**1. Create an X app** at [console.x.com](https://console.x.com):
- New **Project** → new **App** inside it
- **User authentication settings** → enable **OAuth 2.0**, Type: *Web App*
- **Callback URI:** `http://localhost:3000/callback`
- Permissions: **Read and write**
- Copy the **OAuth 2.0 Client ID** (and Secret) from **Keys and tokens**
- Add $10–20 prepaid credits under **Billing**

**2. Install:**
```sh
cp -r x ~/workspace/skills/
```

**3. Connect:**
```sh
python3 ~/workspace/skills/x/bin/x.py auth login --client-id <your-client-id>
```
Approve in your browser, paste back the code, enter your client secret.
Done — tokens refresh automatically, forever. Verify with:
```sh
python3 ~/workspace/skills/x/bin/x.py me
```

## Use it

```sh
python3 ~/workspace/skills/x/bin/x.py post --text "Hello" --dry-run  # cost preview
python3 ~/workspace/skills/x/bin/x.py post --text "Hello"             # publish
python3 ~/workspace/skills/x/bin/x.py timeline
python3 ~/workspace/skills/x/bin/x.py mentions
python3 ~/workspace/skills/x/bin/x.py search --query "AI"
python3 ~/workspace/skills/x/bin/x.py like --id <post_id>
```

Every command takes `--json`. Every command also takes `--sandbox` to run
against a local mock API — no login, no spend, nothing published.

## What it costs

X bills pay-per-use. A few posts a day ≈ **$1–2/month**.

| Action | Cost |
|---|---|
| Text post | $0.015 |
| Post with a link | **$0.20** |
| Reading posts | $0.005 each |

## If something breaks

- **X says "Something went wrong" on approve** → the callback URL isn't
  registered exactly as `http://localhost:3000/callback`, or OAuth 2.0 isn't
  enabled, or you used the API Key instead of the OAuth 2.0 Client ID.
- **403 "App must be attached to a Project"** → attach the app to a Project
  in console.x.com.
- **401 errors** → the skill auto-refreshes; if it persists, re-run
  `auth login`.

## License

MIT

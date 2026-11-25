# Discord ↔ Maestro bridge (Phase 4)

Chat with a Maestro agent from a Discord channel instead of typing in Maestro.
This is **two-way**: your message → `maestro-cli send <agent> ...` → reply posted
back. It is separate from the harness **webhook**, which is one-way alerts.

## 1. Create the Discord bot

1. Open https://discord.com/developers/applications → **New Application**.
2. **Bot** tab → **Add Bot** → copy the **token** (you won't see it again).
3. Under **Privileged Gateway Intents**, enable **MESSAGE CONTENT INTENT** (required to read messages).
4. **OAuth2 → URL Generator**:
   - Scopes: `bot`
   - Permissions: **View Channels**, **Send Messages**, **Read Message History**
   - Open the generated URL and invite the bot to your server.

## 2. Get IDs

In Discord: **Settings → Advanced → Developer Mode = ON**, then right-click
your username → **Copy ID**, and right-click the channel → **Copy ID**.

## 3. Configure

```sh
cd ~/Projects/hackbots-starter
cp .env.example .env          # then edit it
# set DISCORD_BOT_TOKEN, ALLOWED_USER_IDS, ALLOWED_CHANNEL_IDS
set -a; . ./.env; set +a
```

At minimum `DISCORD_BOT_TOKEN` and `ALLOWED_USER_IDS` must be set. If
`ALLOWED_USERS` is empty the bot refuses every request.

## 4. Run

```sh
/home/muhammadibn3bdullah/.local/share/mdtools/venv/bin/python discord_bot/bridge.py --check
/home/muhammadibn3bdullah/.local/share/mdtools/venv/bin/python discord_bot/bridge.py
```

The machine must stay awake with Maestro running (same as before).

## 5. Use it in Discord

```
!help          → usage
!status        → maestro-cli auto-run-status --agent hackbot-lead
any message    → forwarded to the agent, reply posted back
```

## Safety

- Only allowlisted users/channels are served.
- The bot runs the agent with whatever scope you give it. Do not use real targets
  without written authorization — the agent can read the recorded scope from the
  harness with `get_target_scope` / `check_scope`.
- Never commit the bot token. `.env` is in `.gitignore`.

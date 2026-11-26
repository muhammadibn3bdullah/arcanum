# Authentication and authorization

Three separate concerns are easy to confuse: **provider authentication**
(which model service answers), **integration credentials** (Discord,
dashboard), and **target authorization** (which systems may be tested).

## Provider authentication

- Default provider is OpenCode (`ARCANUM_LLM_PROVIDER=opencode`). Authenticate
  your model provider with `opencode auth login` in the OpenCode configuration
  you use and confirm `opencode run` works for your operator account. This is a
  separate, interactive step: the installer bootstraps Python and installs
  binaries but never stores provider credentials, and reports `Needs
  authentication` until you do this. It is not part of the write-free
  `installers/install.sh --check` preflight.
- Maestro v1.1.1-RC from RunMaestro/Maestro owns agent tabs and dispatch. Point
  `MAESTRO_CLI` at the binary if it is not at `~/.local/bin/maestro-cli`, and
  set `MAESTRO_HEAD_AGENT` only if you intentionally rename the lead agent.
- `OPENCODE_CLI` overrides the OpenCode binary path (default
  `~/.opencode/bin/opencode`).
- Optional local provider: set `ARCANUM_LLM_PROVIDER=ollama` and adjust
  `OLLAMA_URL` / `OLLAMA_MODEL`. This is a local HTTP provider, not an
  internet service.
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and `GOOGLE_API_KEY` in
  [.env.example](../../.env.example) are placeholders only. Fill them only if
  your chosen provider setup explicitly requires them. Never commit values.

## Discord bridge

1. Create an application and bot at <https://discord.com/developers/applications>.
2. Enable **Message Content Intent** under Privileged Gateway Intents; the bot
   needs to read messages to forward them.
3. Invite it with `bot` scope and permissions to view channels, send messages,
   and read message history.
4. Enable Developer Mode in Discord and copy your user ID and channel ID.
5. In `.env`, set `DISCORD_BOT_TOKEN`, `ALLOWED_USER_IDS`, and
   `ALLOWED_CHANNEL_IDS` (comma-separated). The bridge checks these lists when
   they are set; always configure a user allowlist. The bridge's `--check`
   command reports `NOT READY` when the user allowlist is empty, so do not run
   it that way.
6. The machine running Arcanum must stay awake with Maestro running. See
   `integrations/discord/README.md` for the bridge commands.

Rotating a leaked bot token in Discord is the first response; then replace the
value in `.env`.

## Dashboard and ingest

- `DASHBOARD_TOKEN` gates `/api/*` (health and ingest are exceptions). Open
  the dashboard once with `?token=<value>` so the UI stores it locally.
- `INGEST_TOKEN` is the bearer token for `POST /api/ingest`.
- `DISCORD_WEBHOOK_URL` is optional and used for one-way alert posts.
- The dashboard binds to `127.0.0.1` by default. Do not expose it publicly
  without authentication in front of it.

## Target authorization

- Keep each engagement's scope record in the engagement folder. The Scope
  Manager resolves authorized domains from trusted records; agents cannot
  expand scope through messages.
- `DYNAMIC_TARGET_MODE=operator_declared` (default) registers domains sent by
  allowlisted operators (`ARCANUM_ALLOWED_OPERATORS`) as authorized targets;
  use `target_allowlist_only` with `ARCANUM_TARGET_ALLOWLIST` to restrict this.
- Passive collection is the default posture. Active testing and cross-user
  validation require explicit human approval through the HITL gate; a denial
  stops the phase.
- An authorization grant (`grant_id`) is issued for a single phase and must
  be used with the gateway tool in that same phase. Never forge or reuse one.

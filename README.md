# Arcanum

Arcanum is an independent, local-first orchestration framework for authorized
security testing. It coordinates five specialized agents through Maestro, uses
OpenCode as the specialist execution layer, and refuses to treat an agent's
acknowledgment as evidence that work was performed.

> **Authorized use only.** Arcanum is a testing framework, not a license to
> probe systems. Use it only against systems you own or have explicit written
> authorization to test. Keep activity within the recorded scope; active
> testing requires a human approval decision. Never use real target data for
> experiments.

Arcanum is a **new, independent repository**
(<https://github.com/muhammadibn3bdullah/arcanum.git>). It is **not a fork** of
Claude-BugHunter. A pinned subset of upstream skills is vendored with
attribution preserved; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and
[docs/skills/vendored.md](docs/skills/vendored.md).

## What it does

- **Maestro-first orchestration.** `hackbot-lead` delegates; four specialists
  (`recon-agent`, `hunt-agent`, `validate-agent`, `report-agent`) do phase work.
- **OpenCode executes specialists.** Provider authentication is handled by the
  configured agent/provider stack, not by trusting agent messages.
- **One conversation per target.** A canonical shared domain room is keyed by
  `(workflow_id, target_id)` and has five agent-facing views. Internal
  Maestro/OpenCode sessions are metadata only, not extra user conversations.
- **Verified completion.** A dispatch acknowledgment, marker, or refusal is
  never a result. The verification gate requires a structured JSON result,
  task-specific acceptance criteria, trusted identity, and cross-target
  isolation.
- **Capability grants.** Tool calls go through a gateway and require a grant
  issued for the same phase by the authorization layer.
- **Bundled skills.** Five Arcanum-authored skills plus 83 pinned upstream
  skills from Claude-BugHunter (88 total in the registry).

## Architecture at a glance

```
Operator / harness / Discord bridge
             |
      Maestro (runtime + tabs)
             |
     hackbot-lead (delegator only)
             |
  +----------+-----------+-----------+
  |          |           |           |
recon-    hunt-      validate-   report-
agent     agent       agent      agent
  |          |           |           |
  +---- OpenCode execution layer ----+
             |
  authorization gateway -> tools (Caido, Burp, HTTP, harness)
             |
  SQLite state (~/.arcanum) + canonical shared rooms
```

Key paths:

| Path | Purpose |
| --- | --- |
| `src/arcanum/` | Python package: core, agents, runtime, permissions, tools, LLM providers |
| `installers/` | `install.sh` (macOS/Linux), `install.ps1` (Windows), shared Python engine |
| `skills/arcanum/` | Five Arcanum-authored skills |
| `skills/vendor/claude-bughunter/` | Pinned upstream skills (attribution retained) |
| `skills/registry/registry.json` | Skill inventory with provenance and content hashes |
| `workflows/maestro/playbooks/` | Maestro cycle playbook |
| `apps/dashboard/` | Dashboard/harness control plane (Bun + React + SQLite) |
| `integrations/discord/` | Two-way Discord ↔ Maestro bridge |
| `integrations/harness/` | Harness ingest/orchestration helpers |
| `scripts/` | Migration, registry generation/validation, wiring and lifecycle helpers |
| `tests/unit/` | Unit tests, including room consistency and completion verification |

See [docs/architecture/README.md](docs/architecture/README.md) for details.

## Requirements

| Dependency | Version / note |
| --- | --- |
| Python | 3.12 or newer |
| Python packages | `pydantic`, `pytest` (dev/tests), `discord.py`, `mcp` |
| Bun | Dashboard/dev server |
| OpenCode | v1.19.6 (specialist execution layer) |
| Maestro | v1.1.1-RC from **RunMaestro/Maestro** (not the mobile.dev project) |
| SQLite | Runtime state; no separate database server |
| Optional | Burp Suite / Caido when their gateway capabilities are used |

Runtime data is written under `~/.arcanum` unless `ARCANUM_HOME` is set.

## Quick start

### Linux / macOS

```sh
git clone https://github.com/muhammadibn3bdullah/arcanum.git
cd arcanum
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e . pytest pydantic discord.py mcp

bash installers/install.sh          # see docs/installation for flags
cp .env.example .env                # edit it; never commit real secrets
python scripts/migrate_database.py  # initializes/upgrades local SQLite state
```

### Windows (PowerShell)

```powershell
git clone https://github.com/muhammadibn3bdullah/arcanum.git
cd arcanum
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e . pytest pydantic discord.py mcp

powershell -ExecutionPolicy Bypass -File .\installers\install.ps1
copy .env.example .env
python scripts\migrate_database.py
```

Installer flags, platform notes, and optional services are documented in
[docs/installation/](docs/installation/README.md). The repository ships a CI
matrix on `ubuntu-latest`, `macos-latest`, and `windows-latest` that runs unit
tests, registry validation, secret scanning, and static/syntax checks. **No
fresh-clone end-to-end installation on every platform is claimed here**; CI
does not install the product on a clean machine.

Run the tests locally with:

```sh
PYTHONPATH=src python -m pytest tests -q
```

## Provider and integration authentication

- Authenticate model providers through the OpenCode/Maestro configuration you
  use (for example OpenCode's own auth flow). `ARCANUM_LLM_PROVIDER` selects
  `opencode` (default) or the optional local `ollama` provider.
- Set `MAESTRO_CLI` and `OPENCODE_CLI` only if the binaries are outside the
  default locations.
- For the Discord bridge, create a bot with the Message Content intent, set
  `DISCORD_BOT_TOKEN`, `ALLOWED_USER_IDS`, and `ALLOWED_CHANNEL_IDS`.
- For the dashboard, set `DASHBOARD_TOKEN` and `INGEST_TOKEN` before exposing
  any API beyond localhost.

Full steps: [docs/installation/authentication.md](docs/installation/authentication.md).
Never commit `.env` or tokens; see [.env.example](.env.example).

## Supported platforms

The declared installer targets are macOS/Linux (`installers/install.sh`) and
Windows (`installers/install.ps1`), backed by a shared Python engine. The
Python core is platform-independent (Python 3.12+, SQLite). The dashboard uses
Bun and binds to localhost by default. Optional Burp/Caido integration requires
those tools to be installed separately. See platform notes in
[docs/installation/](docs/installation/README.md).

## Troubleshooting, upgrade, and removal

- Problems: [docs/troubleshooting/README.md](docs/troubleshooting/README.md)
- Upgrade / uninstall: [docs/installation/upgrade-uninstall.md](docs/installation/upgrade-uninstall.md)

## Licensing and attribution

Licensing terms retained in this repository are in `licenses/` (MIT code
license, CC BY 4.0 content license, and `NOTICE`). Vendored Claude-BugHunter
skills remain under their upstream MIT (code) / CC BY 4.0 (content and docs)
terms with attribution preserved in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and
[docs/skills/vendored.md](docs/skills/vendored.md). Arcanum does not claim
authorship of upstream material and is not affiliated with or endorsed by the
upstream projects or tool vendors.

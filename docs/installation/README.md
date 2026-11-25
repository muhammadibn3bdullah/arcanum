# Installation overview

Arcanum runs locally. Installation consists of four parts:

1. **Python environment** — Python 3.12+ with the core package and dev/tool
   dependencies (`pydantic`, `pytest`, `discord.py`, `mcp`).
2. **Platform installer** — `installers/install.sh` on macOS/Linux or
   `installers/install.ps1` on Windows, backed by a shared Python engine. These
   install the agent/skill wiring used by Maestro and OpenCode.
3. **Local configuration** — copy `.env.example` to `.env` and set tokens,
   paths, and allowlists. Never commit the result.
4. **Local state initialization** — run `python scripts/migrate_database.py`.
   Runtime databases live under `~/.arcanum` unless `ARCANUM_HOME` is set.

Optional components: the dashboard (`apps/dashboard`, requires Bun), the
Discord bridge (`integrations/discord`, requires a Discord bot), and Burp/Caido
services used by the gateway capabilities.

## Guides

| Guide | Contents |
| --- | --- |
| [linux.md](linux.md) | Linux prerequisites and install steps |
| [macos.md](macos.md) | macOS prerequisites and install steps |
| [windows.md](windows.md) | Windows/PowerShell install steps |
| [authentication.md](authentication.md) | Provider, Discord, dashboard, and scope authorization |
| [upgrade-uninstall.md](upgrade-uninstall.md) | Upgrades, data migration, and removal |

## Scope of verification

The repository CI matrix checks Python tests, skill-registry consistency,
secret patterns, and static syntax for installers on
`ubuntu-latest`/`macos-latest`/`windows-latest`. It does **not** perform a
fresh-clone end-to-end installation on each OS. Treat the platform guides as
the intended procedure, not as a record of completed cross-platform runs.

# Installation overview

Arcanum runs locally. Installation consists of four parts:

1. **Python environment** — no Python prerequisite: the installer reuses a host
   Python 3.12+ or bootstraps a pinned, checksum-verified standalone CPython
   under `<ARCANUM_HOME>/runtime`, then installs the core package and dev/tool
   dependencies (`pydantic`, `pytest`, `discord.py`, `mcp`) in a venv.
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

## Supported platforms

| Platform | Install entry point | Automation |
| --- | --- | --- |
| Linux x86_64 / ARM64 | `bash installers/install.sh` | Full, no sudo |
| macOS x64 / ARM64 | `bash installers/install.sh` | Full, no sudo |
| Windows x64 | Windows PowerShell 5.1: `powershell -ExecutionPolicy Bypass -File .\installers\install.ps1` | Full; Maestro needs a manual run |
| Windows ARM64 | — | **Unsupported** — Maestro v1.1.1-RC has no matching artifact |

Linux/macOS bootstrap needs `curl` or `wget`, `tar`, `awk`, `wc`, and
`sha256sum` or `shasum`. Windows needs the PowerShell 5.1 builtins
`Invoke-WebRequest` / `Get-FileHash` and `tar.exe`. These are OS-level host
tools, not a Python prerequisite; if one is missing the installer prints
`UNSUPPORTED` with a manual fallback. Skills land in the namespaced roots
`<home>/skills/arcanum` and `<home>/skills/vendor/claude-bughunter`, while
OpenCode discovery remains a flat `<opencode-config>/skills` directory.

## Guides

| Guide | Contents |
| --- | --- |
| [linux.md](linux.md) | Linux prerequisites and install steps |
| [macos.md](macos.md) | macOS prerequisites and install steps |
| [windows.md](windows.md) | Windows/PowerShell install steps |
| [authentication.md](authentication.md) | Provider, Discord, dashboard, and scope authorization |
| [upgrade-uninstall.md](upgrade-uninstall.md) | Upgrades, data migration, and removal |

## Scope of verification

The repository CI matrix runs on `ubuntu-latest`, `macos-latest`, and
`windows-latest`. It validates the bootstrap manifest (`python.bootstrap` pins),
checks bash syntax, parses `install.ps1` / `uninstall.ps1` / `bootstrap.ps1`
with Windows PowerShell 5.1, verifies that `--check` is write-free, checks the
five-skill/88-skill inventory, and verifies `checksums.txt` when present (it is
generated at release time). It also runs unit tests, skill-registry validation,
and secret scanning. CI runs tests and static checks only: it does **not**
perform a fresh-clone end-to-end installation on any OS, never publishes a
release, and cannot prove desktop Maestro integration in a headless runner.
Treat the platform guides as the intended procedure, not as a record of
completed cross-platform runs.

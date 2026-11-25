# Linux installation

## Prerequisites

- Python 3.12 or newer (from your distribution, pyenv, or another installer).
- Git.
- Maestro CLI v1.1.1-RC from RunMaestro/Maestro and OpenCode v1.19.6, available
  on `PATH` or at the paths set in `.env` (`MAESTRO_CLI`, `OPENCODE_CLI`).
- Bun, only if you plan to run the dashboard in `apps/dashboard`.
- Burp Suite / Caido, only if you use their gateway capabilities.

## Install

```sh
git clone https://github.com/muhammadibn3bdullah/arcanum.git
cd arcanum

python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e . pytest pydantic discord.py mcp

bash installers/install.sh
```

Run `bash installers/install.sh --help` to see the options this installer
actually ships with; do not assume flags from other projects.

## Configure and initialize state

```sh
cp .env.example .env
# edit .env: tokens, allowlisted operators/targets, CLI paths
python scripts/migrate_database.py
```

The migration helper creates or adds missing tables under `ARCANUM_HOME`
(default `~/.arcanum`) without overwriting existing rows. It reports the
database and tab-registry paths it used.

## Optional services

- Dashboard: `cd apps/dashboard && bun install && bun run dev` (binds to
  `127.0.0.1` by default; set `DASHBOARD_TOKEN` before any wider exposure).
- Burp/Caido helper: `bash scripts/start_services.sh` (requires those tools to
  be installed separately).

## Verify locally

```sh
PYTHONPATH=src python -m pytest tests -q
python scripts/validate_skill_registry.py
```

See [troubleshooting](../troubleshooting/README.md) if a CLI is not found or a
skill fails to load. No claim is made that this exact sequence has been run on
every Linux distribution.

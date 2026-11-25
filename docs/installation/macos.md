# macOS installation

## Prerequisites

- macOS with Python 3.12+ available. Homebrew is one option:
  `brew install python@3.12 git`.
- Maestro CLI v1.1.1-RC (RunMaestro/Maestro) and OpenCode v1.19.6, on `PATH`
  or configured in `.env`.
- Bun, only for the dashboard: `brew install bun` or the upstream Bun
  installer.
- Burp Suite / Caido, only for the corresponding gateway capabilities.

## Install

```sh
git clone https://github.com/muhammadibn3bdullah/arcanum.git
cd arcanum

python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e . pytest pydantic discord.py mcp

bash installers/install.sh
```

Use `bash installers/install.sh --help` for the installer's supported options.

## Configure and initialize state

```sh
cp .env.example .env
# edit .env, then:
python scripts/migrate_database.py
```

Runtime databases default to `~/.arcanum/arcanum.db`,
`~/.arcanum/arcanum-approvals.sqlite`, and
`~/.arcanum/arcanum-tabs.sqlite`. Override them in `.env` with absolute paths
if needed.

## Optional services and checks

```sh
cd apps/dashboard && bun install && bun run dev   # dashboard on 127.0.0.1:13337 by default
PYTHONPATH=src python -m pytest tests -q
python scripts/validate_skill_registry.py
```

macOS is part of the repository's CI matrix for tests and static checks; the
CI matrix does not run the interactive installer on a clean machine.

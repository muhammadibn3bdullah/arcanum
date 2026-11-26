# macOS installation

## Prerequisites

- macOS (x64 or ARM64). **No Python prerequisite**: `install.sh` reuses a host
  Python 3.12+ or bootstraps a pinned, SHA-256-verified standalone CPython into
  `<ARCANUM_HOME>/runtime`. Host tools (OS-level, not Python): `curl` or `wget`,
  `tar`, `awk`, `wc`, and `shasum` (or `sha256sum`). If any is missing, the
  installer prints `UNSUPPORTED` with a manual fallback and downloads nothing.
  Homebrew (`brew install git`) is one way to supply Git.
- Maestro CLI v1.1.1-RC (RunMaestro/Maestro) and OpenCode v1.19.6, on `PATH`
  or configured in `.env`.
- Bun, only for the dashboard: `brew install bun` or the upstream Bun
  installer.
- Burp Suite / Caido, only for the corresponding gateway capabilities.

## Install

```sh
git clone https://github.com/muhammadibn3bdullah/arcanum.git
cd arcanum

bash installers/install.sh --check --non-interactive  # write-free preflight
bash installers/install.sh                           # bootstrap Python if needed
```

Use `bash installers/install.sh --help` for the installer's supported options.
`--check` is write-free: it never downloads, extracts, creates directories, or
writes a report.

### Manual fallback

If the bootstrap cannot run (a host tool is missing or there is no pinned asset
for your architecture), install Python 3.12+ yourself, create a venv with
`python3 -m venv .venv && . .venv/bin/activate && python -m pip install -e . pytest pydantic discord.py mcp`,
set `ARCANUM_PYTHON` to that interpreter, and re-run `install.sh`. An existing
interpreter or venv is never overwritten. After install, all five Arcanum skills
and the 83 vendored skills (88 total) are hash-verified and locally modified
skills are preserved. Provider authentication is separate: run
`opencode auth login` yourself after installing.

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

# Linux installation

## Prerequisites

- **No Python prerequisite.** `install.sh` reuses a host Python 3.12+ or
  bootstraps a pinned, SHA-256-verified standalone CPython into
  `<ARCANUM_HOME>/runtime`. Host tools (OS-level, not Python): `curl` or `wget`,
  `tar`, `awk`, `wc`, and `sha256sum` or `shasum`. If any is missing, the
  installer prints `UNSUPPORTED` with a manual fallback and downloads nothing.
- Git.
- Maestro CLI v1.1.1-RC from RunMaestro/Maestro and OpenCode v1.19.6, available
  on `PATH` or at the paths set in `.env` (`MAESTRO_CLI`, `OPENCODE_CLI`).
- Bun, only if you plan to run the dashboard in `apps/dashboard`.
- Burp Suite / Caido, only if you use their gateway capabilities.

## Install

```sh
git clone https://github.com/muhammadibn3bdullah/arcanum.git
cd arcanum

bash installers/install.sh --check --non-interactive  # write-free preflight
bash installers/install.sh                           # bootstrap Python if needed
```

Run `bash installers/install.sh --help` to see the options this installer
actually ships with; do not assume flags from other projects. `--check` never
downloads, extracts, creates directories, or writes a report.

### Manual fallback

If the bootstrap cannot run (a host tool is missing or there is no pinned asset
for your architecture), install Python 3.12+ yourself, create a venv with
`python3 -m venv .venv && . .venv/bin/activate && python -m pip install -e . pytest pydantic discord.py mcp`,
set `ARCANUM_PYTHON` to that interpreter, and re-run `install.sh`. An existing
interpreter or venv is never overwritten. The installer verifies all five
Arcanum skills and the 83 vendored skills (88 total) after install and preserves
locally modified skills instead of overwriting them. Provider authentication is
separate: run `opencode auth login` yourself after installing.

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

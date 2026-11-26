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
| Python | 3.12 or newer, but **no Python prerequisite** for the installers — they bootstrap a pinned standalone CPython when the host has none |
| Python packages | `pydantic`, `pytest` (dev/tests), `discord.py`, `mcp` |
| Bun | Dashboard/dev server |
| OpenCode | v1.19.6 (specialist execution layer) |
| Maestro | v1.1.1-RC from **RunMaestro/Maestro** (not the mobile.dev project) |
| SQLite | Runtime state; no separate database server |
| Optional | Burp Suite / Caido when their gateway capabilities are used |

Runtime data is written under `~/.arcanum` unless `ARCANUM_HOME` is set.

## Quick start

The installers have **no Python prerequisite** and use one entry point per
platform:

```sh
# Linux / macOS — write-free preflight, then install
git clone https://github.com/muhammadibn3bdullah/arcanum.git
cd arcanum
bash installers/install.sh --check --non-interactive
bash installers/install.sh
```

```powershell
# Windows — Windows PowerShell 5.1 (PowerShell 7 / pwsh is optional)
git clone https://github.com/muhammadibn3bdullah/arcanum.git
cd arcanum
powershell -ExecutionPolicy Bypass -File .\installers\install.ps1 -Check -NonInteractive
powershell -ExecutionPolicy Bypass -File .\installers\install.ps1
```

The installer reuses a host Python 3.12+ when present; otherwise it downloads
a pinned, SHA-256-verified standalone CPython into `<ARCANUM_HOME>/runtime` and
runs the shared engine with that interpreter. Runtime state, databases, and the
Discord `.env` template are created under `ARCANUM_HOME` (default `~/.arcanum`);
the checkout is read-only apart from your own local configuration.

Installer flags, manual fallback, platform notes, and optional services are
documented in [docs/installation/](docs/installation/README.md). The repository
ships a CI matrix on `ubuntu-latest`, `macos-latest`, and `windows-latest` that
validates the bootstrap manifest, checks bash syntax and Windows PowerShell 5.1
parsing, verifies `--check` writes nothing, checks the five-skill inventory and
`checksums.txt`, and runs unit tests, registry validation, and secret scanning.
**No fresh-clone end-to-end installation on every platform is claimed here**: CI
runs tests and static checks only, never publishes a release, and cannot prove
desktop Maestro integration in a headless runner.

## Install details (zero prerequisite)

- **Bootstrap:** without a host Python 3.12+, the entry point fetches the exact
  asset pinned in `config/dependencies.json` (`astral-sh/python-build-standalone`
  CPython 3.12.16), verifies its SHA-256 and byte size, extracts it under
  `<ARCANUM_HOME>/runtime`, checks `python --version`, and passes the interpreter
  to `install_core.py` via `--python` / `ARCANUM_BOOTSTRAP_PYTHON`. Nothing is
  piped into a shell and no admin rights are requested.
- **Host tooling (not a Python prerequisite):** Linux/macOS need `curl` or
  `wget`, `tar`, `awk`, `wc`, and `sha256sum` or `shasum`; Windows PowerShell 5.1
  needs `Invoke-WebRequest`, `Get-FileHash`, and `tar.exe`. If any is missing,
  the installer reports `UNSUPPORTED` with a manual fallback instead of
  downloading anything.
- **Write-free check:** `--check` (or `-Check`) never downloads, extracts,
  creates directories, or writes a report. With no suitable Python it reports
  `Needs manual action` for the downstream stages.
- **Skills:** installed skills live in the namespaced roots
  `<home>/skills/arcanum` and `<home>/skills/vendor/claude-bughunter`, while
  OpenCode discovery stays a flat `<opencode-config>/skills` directory. The five
  Arcanum skills (`api-fingerprint-caido`, `interceptor`, `js_analysis`,
  `lazy_chunk_downloader`, `say_hello`) are kept separate from the 83 pinned
  vendored BugHunter skills (88 total); every file hash and content hash is
  verified after install.
- **Windows ARM64 is unsupported** because Maestro v1.1.1-RC has no matching
  artifact. The installer reports `Unsupported` explicitly and never
  substitutes the unrelated mobile.dev CLI.
- **Provider auth is separate:** the installer never stores model-provider
  credentials. After install, authenticate yourself with `opencode auth login`
  (or your OpenCode profile); until then the installer reports
  `Needs authentication`.

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

The declared installer targets are macOS/Linux x64 and ARM64
(`installers/install.sh`) and Windows x64 (`installers/install.ps1`), backed by
a shared Python engine. The Python core is platform-independent (Python 3.12+,
SQLite). **Windows ARM64 is unsupported** because Maestro v1.1.1-RC publishes no
matching artifact; the installer reports `Unsupported` and offers a manual
fallback. The dashboard uses Bun and binds to localhost by default. Optional
Burp/Caido integration requires those tools to be installed separately. See the
platform matrix and host-tool notes in
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

# Arcanum standalone installer

Thin entry points (`install.sh` / `install.ps1`, `uninstall.sh` / `uninstall.ps1`)
call the shared standard-library engine `modules/install_core.py`. All runtime
state lives under `ARCANUM_HOME` (default `~/.arcanum`); the repository is only
read from.

## Quick start

```bash
# macOS / Linux — preflight only, writes nothing
./installers/install.sh --check --non-interactive

# Install (prompts for Discord token and provider auth)
./installers/install.sh

# Fully automated / CI
./installers/install.sh --non-interactive --no-start-dashboard
```

```powershell
# Windows — Windows PowerShell 5.1 is the supported baseline
powershell -ExecutionPolicy Bypass -File .\installers\install.ps1 -Check -NonInteractive
powershell -ExecutionPolicy Bypass -File .\installers\install.ps1
# PowerShell 7 is optional: pwsh ./installers/install.ps1 -Check
```

## Zero-prerequisite bootstrap

`install.sh` and `install.ps1` do not require Python. They first reuse an
existing Python >= 3.12 (explicit `ARCANUM_PYTHON`, an existing venv, or one on
`PATH`); otherwise they bootstrap the pinned standalone CPython described by
`python.bootstrap` in `../config/dependencies.json` (CPython 3.12.16 from
`astral-sh/python-build-standalone`) into `<ARCANUM_HOME>/runtime`. The archive
SHA-256 and byte size are verified before extraction, and the interpreter is
passed to the engine via `--python` / `ARCANUM_BOOTSTRAP_PYTHON`. No remote
script is piped into a shell and no admin rights are requested.

Host tools are still required at the OS level, which is not a Python
prerequisite:

- Linux/macOS: `curl` or `wget`, `tar`, `awk`, `wc`, and `sha256sum` or `shasum`.
- Windows: PowerShell 5.1 builtins `Invoke-WebRequest` and `Get-FileHash`, plus
  `tar.exe` (Windows 10 1803+).

When a host tool is missing, or a platform has no pinned artifact, the installer
prints `UNSUPPORTED` with a **manual** fallback and makes no download. **Windows
ARM64 is unsupported** overall because Maestro v1.1.1-RC has no matching
artifact. `--check` / `-Check` stays **write-free**: it never downloads,
extracts, creates directories, or writes a report; without a suitable Python it
reports `Needs manual action` for the downstream stages.

Useful flags: `--home PATH` (override `ARCANUM_HOME`), `--prefix PATH` (move
`bin/` and `tools/`), `--repo PATH`, `--opencode-config PATH`,
`--maestro-config PATH`, `--data-dir PATH`, `--python PATH` (or
`ARCANUM_BOOTSTRAP_PYTHON` / `ARCANUM_PYTHON`), `--skip-downloads`,
`--skip-tests`, `--no-start-dashboard`, `--non-interactive`, `--check`,
`--timeout SECONDS`. Both uninstall wrappers accept `--check` for a dry run.

## Status matrix

The engine prints a matrix with these values: `Installed`, `Verified`,
`Needs authentication`, `Needs manual action`, `Unsupported`, `Failed`,
`Already present`, `Skipped`. A machine-readable copy is written to
`<home>/install-report.json` after a real install/uninstall (not in `--check`).
`Failed` or `Unsupported` yields exit code 2.

## Supported platforms

| Platform | Bun 1.4.5 | OpenCode 1.19.6 | Maestro 1.1.1-RC | Automation |
| --- | --- | --- | --- | --- |
| macOS arm64 | pinned `.zip` | pinned `.zip` | `.zip` (dmg listed) | Full |
| macOS x64 | pinned `.zip` | pinned `.zip` | `.zip` (dmg listed) | Full |
| Linux x86_64 | pinned `.zip` | pinned `.tar.gz` | `.deb` extract / AppImage fallback | Full, no sudo |
| Linux arm64 | pinned `.zip` | pinned `.tar.gz` | `.deb` extract / AppImage fallback | Full, no sudo |
| Windows x64 | pinned `.zip` | pinned `.zip` | portable/setup `.exe` downloaded + checksum-verified | Maestro needs manual run |
| Windows ARM64 | pinned `.zip` | pinned `.zip` | **Unsupported** — no official asset | Reported explicitly |

The unrelated **mobile.dev Maestro CLI is never installed**: no `get.mobile.dev`
script is fetched and no mobile.dev/npm CLI is executed.

## What was verified

* **OpenCode repo:** on 2026-11-25 the GitHub API returned `301` for
  `sst/opencode` → `/repositories/975734319` and `https://github.com/sst/opencode`
  → `https://github.com/anomalyco/opencode`. The `v1.19.6` release is immutable,
  published 2026-11-17, with `html_url` on `anomalyco/opencode`. All six
  requested CLI assets are there. The manifest uses canonical `anomalyco/opencode`
  URLs and records `sst/opencode` as a redirect alias.
* **Checksums:** every binary asset SHA-256 in `../config/dependencies.json` was
  compared against the live GitHub Releases API on 2026-11-25 (6 OpenCode + 6
  Bun + 12 Maestro assets, zero mismatches). Python packages are pinned by exact
  version and verified by `pip` over TLS.
* **Agent prompts:** all five `system` strings were extracted by importing
  `scripts/wire_maestro_agents.py` without running `main()`, then copied
  byte-for-byte into `../config/opencode.agents.json` and
  `../config/agents/*.md`. The installer re-checks that equality before writing.
* **Skills:** the installer writes to the namespaced roots
  `<home>/skills/arcanum` and `<home>/skills/vendor/claude-bughunter` and
  verifies all five Arcanum skills plus the 83 pinned vendored skills (88 total)
  by per-file and content hash. OpenCode discovery remains a flat
  `<opencode-config>/skills` directory.

## Known limitations / manual steps

1. The verbatim `hackbot-lead` SYSTEM prompt contains the upstream absolute path
   `arcanum-tab-dispatch`.
   The prompt is intentionally not altered. On another machine the installer
   also permits `<home>/bin/arcanum-tab-dispatch` in permissions and reports
   `Needs manual action` if the legacy path is absent. Update the prompt
   deliberately if you want agents to use only the new path.
2. Windows Maestro has no silent, no-admin automated setup in this installer.
   The pinned portable `.exe` is downloaded and checksum-verified; run it as the
   current user and re-run `install.ps1` to verify. Windows ARM64 is Unsupported.
3. `skills/registry/registry.json` is authored by another agent. The installer
   reads it when present; a missing registry is non-fatal and reported.
4. Provider authentication (`opencode auth login`) and the Discord bot token are
   interactive. With `--non-interactive` they are reported as
   `Needs authentication`; the Discord template is still created at
   `<home>/discord/.env` with mode `0600` on POSIX.
5. The venv is created with the newest available Python >= 3.12 (the project
   minimum). The engine itself runs on Python >= 3.10.

## Uninstall

`uninstall.sh` / `uninstall.ps1` remove only tracked installer artifacts
(wrappers, venv, tools, copied runtime code, installed skill copies and
playbooks) and restore OpenCode's managed agent fields, always writing a
timestamped backup first. Databases, `data/`, logs, download cache and the
Discord `.env` are deliberately preserved as runtime data.

# Windows installation (PowerShell)

## Prerequisites

- **No Python prerequisite.** `install.ps1` uses **Windows PowerShell 5.1** (the
  `powershell -File` baseline; PowerShell 7 / `pwsh` is optional). It reuses a
  host Python 3.12+ or bootstraps a pinned, SHA-256-verified standalone CPython
  into `<ARCANUM_HOME>/runtime`. Host tools (OS-level, not Python):
  `Invoke-WebRequest`, `Get-FileHash`, and `tar.exe` (Windows 10 1803+). If any
  is missing, the installer prints `UNSUPPORTED` with a manual fallback.
- **Windows ARM64 is unsupported** — Maestro v1.1.1-RC publishes no matching
  artifact. The installer reports `Unsupported` and never substitutes the
  unrelated mobile.dev CLI.
- Git for Windows.
- Maestro CLI v1.1.1-RC (RunMaestro/Maestro) and OpenCode v1.19.6. Set
  `MAESTRO_CLI` / `OPENCODE_CLI` in `.env` with absolute paths when they are
  not on `PATH`.
- Bun, only if you plan to run the dashboard.
- Git Bash (included with Git for Windows) if you need to run the repository's
  `.sh` helper scripts.

## Install

```powershell
git clone https://github.com/muhammadibn3bdullah/arcanum.git
cd arcanum
powershell -ExecutionPolicy Bypass -File .\installers\install.ps1 -Check -NonInteractive
powershell -ExecutionPolicy Bypass -File .\installers\install.ps1
```

If execution policies block local scripts, use `-ExecutionPolicy Bypass` as
shown, or set a scoped policy for the current user after reviewing the script.
The options this installer actually defines are listed in
[installers/README.md](../../installers/README.md). `-Check` is write-free: it
never downloads, extracts, creates directories, or writes a report; without a
suitable Python it reports `Needs manual action` for the downstream stages.

### Manual fallback

If the bootstrap cannot run (a host tool is missing), install Python 3.12+ with
the `py` launcher, create a venv with
`py -3.12 -m venv .venv; .\.venv\Scripts\Activate.ps1; python -m pip install -e . pytest pydantic discord.py mcp`,
set `ARCANUM_PYTHON` to that interpreter, and re-run `install.ps1`. An existing
interpreter or venv is never overwritten. All five Arcanum skills and the 83
vendored skills (88 total) are hash-verified after install; locally modified
skills are preserved. Provider authentication is separate: run
`opencode auth login` yourself after installing.

## Configure and initialize state

```powershell
copy .env.example .env
# edit .env with absolute paths for Windows
python scripts\migrate_database.py
```

Use forward slashes or escaped backslashes for any path values consumed by
Python configuration.

## Checks

```powershell
$env:PYTHONPATH = "src"
python -m pytest tests -q
python scripts\validate_skill_registry.py
```

CI includes a `windows-latest` matrix job that runs the Python tests and parses
`installers/install.ps1`, `installers/uninstall.ps1`, and
`installers/bootstrap/bootstrap.ps1` with Windows PowerShell 5.1. It also
checks the bootstrap manifest, bash syntax, write-free `--check`, the
five-skill/88-skill inventory, and `checksums.txt`, and never publishes a
release. It does not claim a completed interactive install on Windows or desktop
Maestro integration in a headless runner.

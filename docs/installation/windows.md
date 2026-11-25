# Windows installation (PowerShell)

## Prerequisites

- Python 3.12+ (for example `py -3.12 --version` from the Python launcher).
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
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e . pytest pydantic discord.py mcp

powershell -ExecutionPolicy Bypass -File .\installers\install.ps1
```

If execution policies block local scripts, use `-ExecutionPolicy Bypass` as
shown, or set a scoped policy for the current user after reviewing the script.
Run `powershell -File .\installers\install.ps1 -Help` (or consult the script's
parameter block) for the options that this installer actually defines.

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

CI includes a `windows-latest` matrix job that runs the Python tests and
parses `installers/install.ps1` with the PowerShell parser when `pwsh` is
available. It does not claim a completed interactive install on Windows.

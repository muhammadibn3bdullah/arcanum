#!/usr/bin/env pwsh
# Arcanum standalone installer (Windows / PowerShell 7 entry point).
# Thin wrapper: all work is done by modules/install_core.py.
# Python is resolved from PATH (python, then python3, then the py launcher).
$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir
$Core      = Join-Path $ScriptDir 'modules/install_core.py'

if ($PSVersionTable.PSVersion.Major -lt 7) {
    Write-Warning 'PowerShell 7 or newer is recommended; continuing with the available host.'
}

$PythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($null -ne $PythonCmd) {
    & $PythonCmd.Source $Core install --repo $RepoRoot @args
    exit $LASTEXITCODE
}

$Python3Cmd = Get-Command python3 -ErrorAction SilentlyContinue
if ($null -ne $Python3Cmd) {
    & $Python3Cmd.Source $Core install --repo $RepoRoot @args
    exit $LASTEXITCODE
}

$PyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($null -ne $PyLauncher) {
    & $PyLauncher.Source -3 $Core install --repo $RepoRoot @args
    exit $LASTEXITCODE
}

Write-Error 'Python not found on PATH. Install Python 3.12+ and re-run.'
exit 127

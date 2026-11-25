#!/usr/bin/env pwsh
# Arcanum standalone uninstaller (Windows / PowerShell 7 entry point).
# Non-destructive: runtime data, databases and Discord secrets are preserved.
$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir
$Core      = Join-Path $ScriptDir 'modules/install_core.py'

$PythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($null -ne $PythonCmd) {
    & $PythonCmd.Source $Core uninstall --repo $RepoRoot @args
    exit $LASTEXITCODE
}

$Python3Cmd = Get-Command python3 -ErrorAction SilentlyContinue
if ($null -ne $Python3Cmd) {
    & $Python3Cmd.Source $Core uninstall --repo $RepoRoot @args
    exit $LASTEXITCODE
}

$PyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($null -ne $PyLauncher) {
    & $PyLauncher.Source -3 $Core uninstall --repo $RepoRoot @args
    exit $LASTEXITCODE
}

Write-Error 'Python not found on PATH. Install Python 3.12+ and re-run.'
exit 127

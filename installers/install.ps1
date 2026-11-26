#!/usr/bin/env powershell
# Arcanum standalone installer (Windows / PowerShell 5.1 entry point).
#
# Zero-prerequisite flow:
#   1. map the host to windows-x64 / windows-arm64 and read the pinned manifest;
#   2. report Windows ARM64 as UNSUPPORTED for the full install;
#   3. use an existing Python >=3.12 when available;
#   4. otherwise bootstrap a pinned, checksum-verified standalone CPython under
#      ARCANUM_HOME/runtime;
#   5. run installers/modules/install_core.py with that interpreter.
# `-Check` stays strictly write-free: it never downloads, extracts or writes.
$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir
$Core      = Join-Path $ScriptDir 'modules/install_core.py'
$BootstrapScript = Join-Path $ScriptDir 'bootstrap/bootstrap.ps1'
. $BootstrapScript

$CheckOnly = $false
$EngineArgs = @()
foreach ($arg in $args) {
    switch ($arg) {
        '--check' { $CheckOnly = $true; $EngineArgs += '--check' }
        '-Check' { $CheckOnly = $true; $EngineArgs += '--check' }
        '-check' { $CheckOnly = $true; $EngineArgs += '--check' }
        '--non-interactive' { $EngineArgs += '--non-interactive' }
        '-NonInteractive' { $EngineArgs += '--non-interactive' }
        '-noninteractive' { $EngineArgs += '--non-interactive' }
        default { $EngineArgs += $arg }
    }
}

$ArcHome = $env:ARCANUM_HOME
if ([string]::IsNullOrWhiteSpace($ArcHome)) {
    $ArcHome = Join-Path $HOME '.arcanum'
}
$ManifestPath = Join-Path $RepoRoot 'config/dependencies.json'

# BEGIN CHECK-ONLY BRANCH (write-free)
$AssetKey = Get-ArcPlatformAssetKey
try {
    Assert-ArcFullInstallSupport -ManifestPath $ManifestPath -Key $AssetKey
} catch {
    $reason = $_.Exception.Message
    if ($CheckOnly) {
        Write-Output ("[{0,20}] Windows bootstrap: Unsupported - " -f $reason)
        exit 0
    }
    [Console]::Error.WriteLine($reason)
    exit 2
}

$HostPython = Get-ArcHostPython -InstallHome $ArcHome -RepoRoot $RepoRoot

if ($CheckOnly) {
    if ($null -ne $HostPython) {
        & $HostPython $Core install --repo $RepoRoot @EngineArgs
        exit $LASTEXITCODE
    }
    Write-ArcWriteFreeCheck -InstallHome $ArcHome -RepoRoot $RepoRoot -Key $AssetKey
    [Console]::Error.WriteLine('NOTE: downstream stages were not checked because no Python >=3.12 is available yet.')
    exit 0
}
# END CHECK-ONLY BRANCH

$BootstrapPython = $HostPython
if ($null -eq $BootstrapPython) {
    try {
        $BootstrapPython = Get-ArcBootstrapPython -InstallHome $ArcHome -RepoRoot $RepoRoot -Key $AssetKey
    } catch {
        [Console]::Error.WriteLine($_.Exception.Message)
        exit 2
    }
    # The installer engine reads this to create the venv with this interpreter.
    $env:ARCANUM_BOOTSTRAP_PYTHON = $BootstrapPython
}

& $BootstrapPython $Core install --repo $RepoRoot @EngineArgs
exit $LASTEXITCODE

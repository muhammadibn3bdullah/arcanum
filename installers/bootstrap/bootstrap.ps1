#!/usr/bin/env powershell
# Arcanum Windows Python bootstrap helpers (dot-sourced by install.ps1).
#
# Purpose: when the host has no Python >=3.12, download one pinned standalone
# CPython archive from config/dependencies.json, verify its SHA-256, extract it
# under <ARCANUM_HOME>/runtime with tar.exe, verify the interpreter, and return
# its absolute path. Everything else is left to modules/install_core.py.
#
# Windows PowerShell 5.1 is the hard baseline because the documented entry
# point uses `powershell -File ...`. No PowerShell 7-only operators are used.
# Host tooling contract (OS-level, not a Python prerequisite):
#   - Invoke-WebRequest (with -UseBasicParsing on 5.1)
#   - Get-FileHash
#   - tar.exe (Windows 10 1803+; the built-in zip expander cannot read .tar.gz)
# If any is missing, callers get UNSUPPORTED and no download is attempted.
#
# Security rules:
#   - Never pipe a download into a shell.
#   - Never extract an archive before its pinned SHA-256 has been verified.
#   - The pinned URL contains a literal '+' in the asset name. It is passed to
#     Invoke-WebRequest exactly as stored in config/dependencies.json; never
#     percent-encode the plus sign and never decode it as a space.
#   - The manifest "size" field is the compressed archive byte count.
#
# This file only defines functions. Dot-sourcing it has no side effects and
# writes nothing. Pass -Execute to run the small command line entry point.

# Parameter names are prefixed so dot-sourcing cannot overwrite install.ps1
# variables such as $RepoRoot.
[CmdletBinding()]
param(
    [switch] $ExecuteBootstrap,
    [string] $BootstrapHome,
    [string] $BootstrapRepo,
    [string] $BootstrapKey,
    [switch] $BootstrapCheck
)

$ErrorActionPreference = 'Stop'

$ArcPythonMinMajor = 3
$ArcPythonMinMinor = 12

function Write-ArcDiag {
    param([string] $Message)
    [Console]::Error.WriteLine($Message)
}

function Get-ArcPlatformAssetKey {
    $os = $env:OS
    $platform = [System.Environment]::OSVersion.Platform
    if ($os -ne 'Windows_NT' -and $platform -ne [System.PlatformID]::Win32NT) {
        throw 'UNSUPPORTED: the Python bootstrap only supports Windows hosts'
    }

    $arch = $env:PROCESSOR_ARCHITECTURE
    if ([string]::IsNullOrWhiteSpace($arch)) {
        throw 'UNSUPPORTED: PROCESSOR_ARCHITECTURE is required to detect the host architecture'
    }
    if ($arch -eq 'x86' -and -not [string]::IsNullOrWhiteSpace($env:PROCESSOR_ARCHITEW6432)) {
        $arch = $env:PROCESSOR_ARCHITEW6432
    }

    switch ($arch.ToUpperInvariant()) {
        'AMD64' { return 'windows-x64' }
        'ARM64' { return 'windows-arm64' }
        default { throw ('UNSUPPORTED: the Python bootstrap does not support architecture ' + $arch) }
    }
}

function Get-ArcManifestAsset {
    param([string] $ManifestPath, [string] $Key)

    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
        throw ('ERROR: dependency manifest is not readable: ' + $ManifestPath)
    }
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $assets = $manifest.python.bootstrap.assets
    if ($null -eq $assets) {
        throw ('ERROR: python.bootstrap.assets is missing in ' + $ManifestPath)
    }
    $entry = $assets.PSObject.Properties[$Key]
    if ($null -eq $entry) {
        throw ('UNSUPPORTED: no pinned Python asset for ' + $Key + ' in ' + $ManifestPath)
    }

    $a = $entry.Value
    $assetName = [string] $a.asset
    $url = [string] $a.url
    $sha = [string] $a.sha256
    $sizeText = [string] $a.size
    $pythonRelative = [string] $a.python_path
    if ([string]::IsNullOrWhiteSpace($assetName) -or [string]::IsNullOrWhiteSpace($url) -or
        [string]::IsNullOrWhiteSpace($sha) -or [string]::IsNullOrWhiteSpace($sizeText) -or
        [string]::IsNullOrWhiteSpace($pythonRelative)) {
        throw ('ERROR: python.bootstrap.' + $Key + ' is missing asset/url/sha256/size/python_path in ' + $ManifestPath)
    }
    if ($assetName.IndexOfAny([char[]] @('/', '\')) -ge 0) {
        throw ('ERROR: refusing asset name containing a path separator: ' + $assetName)
    }
    if ($sha -notmatch '^[0-9a-fA-F]{64}$') {
        throw ('ERROR: pinned sha256 is not a 64 hex digest for ' + $Key)
    }
    if (-not $url.StartsWith('https://', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw ('ERROR: pinned Python URL must use HTTPS for ' + $Key)
    }
    [long] $size = 0
    if (-not [long]::TryParse($sizeText, [ref] $size) -or $size -le 0) {
        throw ('ERROR: pinned size is not a positive byte count for ' + $Key)
    }
    if ($pythonRelative -notmatch '^python[/\\]python\.exe$') {
        throw ('ERROR: unexpected Windows python_path for ' + $Key + ': ' + $pythonRelative)
    }

    return [pscustomobject] @{
        Asset      = $assetName
        Url        = $url
        Sha256     = $sha
        Size       = $size
        PythonPath = $pythonRelative
    }
}

function Assert-ArcFullInstallSupport {
    param([string] $ManifestPath, [string] $Key)

    # Read the pinned asset first, so a missing manifest entry is reported
    # before any platform decision. This is a read-only operation.
    [void] (Get-ArcManifestAsset -ManifestPath $ManifestPath -Key $Key)
    if ($Key -eq 'windows-arm64') {
        throw 'UNSUPPORTED: Windows ARM64 full install is unsupported because Maestro v1.1.1-RC has no matching artifact; no Python bootstrap was attempted'
    }
}

function Get-ArcFreeSpaceBytes {
    param([string] $Path)

    $full = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetPathRoot($full)
    try {
        $drive = New-Object -TypeName System.IO.DriveInfo -ArgumentList $root
    } catch {
        throw ('UNSUPPORTED: cannot determine free space for ' + $root)
    }
    return [long] $drive.AvailableFreeSpace
}

function Assert-ArcFreeSpace {
    param([string] $Path, [long] $ArchiveSize)

    # Allow roughly four times the compressed size plus a small working margin.
    $required = ($ArchiveSize * 4) + [long] (64 * 1024 * 1024)
    $free = Get-ArcFreeSpaceBytes -Path $Path
    $root = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($Path))
    if ($free -lt $required) {
        throw ('ERROR: insufficient free space on ' + $root + ': need at least ' + $required + ' bytes, found ' + $free)
    }
}

function Assert-ArcHostTools {
    foreach ($tool in @('Invoke-WebRequest', 'Get-FileHash', 'tar.exe')) {
        if ($null -eq (Get-Command $tool -ErrorAction SilentlyContinue)) {
            throw ('UNSUPPORTED: missing required host tool: ' + $tool)
        }
    }
}

# Invoke a native executable without polluting the caller's output stream.
# Native stderr is forwarded to process stderr; the exit code is returned.
function Invoke-ArcNative {
    param([string] $FilePath, [string[]] $Arguments, [switch] $CaptureOutput)

    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($CaptureOutput) {
            $text = (& $FilePath @Arguments 2>&1 | Out-String).Trim()
        } else {
            & $FilePath @Arguments 2>&1 | ForEach-Object { [Console]::Error.WriteLine([string] $_) } | Out-Null
            $text = ''
        }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    return [pscustomobject] @{ Output = $text; ExitCode = $code }
}

function Test-ArcPythonVersion {
    param([string] $Path)

    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    $probe = 'import sys; raise SystemExit(0 if sys.version_info >= (' + $ArcPythonMinMajor + ', ' + $ArcPythonMinMinor + ') else 1)'
    $result = Invoke-ArcNative -FilePath $Path -Arguments @('-c', $probe) -CaptureOutput
    return ($result.ExitCode -eq 0)
}

function Get-ArcVersionText {
    param([string] $Path)

    $result = Invoke-ArcNative -FilePath $Path -Arguments @('--version') -CaptureOutput
    if ($result.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($result.Output)) {
        return 'unknown version'
    }
    if ($result.Output -match 'Python\s+(\d+)\.(\d+)\.(\d+)') {
        return $Matches[0]
    }
    return $result.Output
}

function Get-ArcHostPython {
    param([string] $InstallHome, [string] $RepoRoot)

    $explicit = $env:ARCANUM_PYTHON
    if (-not [string]::IsNullOrWhiteSpace($explicit)) {
        if (-not (Test-ArcPythonVersion -Path $explicit)) {
            throw ('ERROR: ARCANUM_PYTHON=' + $explicit + ' is not an executable Python >=3.12')
        }
        return [System.IO.Path]::GetFullPath($explicit)
    }

    $candidates = @(
        (Join-Path $InstallHome 'venv\Scripts\python.exe'),
        (Join-Path $RepoRoot '.venv\Scripts\python.exe'),
        (Join-Path (Join-Path $InstallHome 'runtime') 'python\python.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-ArcPythonVersion -Path $candidate) {
            return [System.IO.Path]::GetFullPath($candidate)
        }
    }

    foreach ($name in @('python', 'python3')) {
        $cmd = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue
        if ($null -ne $cmd -and (Test-ArcPythonVersion -Path $cmd.Source)) {
            return [System.IO.Path]::GetFullPath($cmd.Source)
        }
    }

    $pyCmd = Get-Command py -CommandType Application -ErrorAction SilentlyContinue
    if ($null -ne $pyCmd) {
        $resolved = Invoke-ArcNative -FilePath $pyCmd.Source -Arguments @('-3', '-c', 'import sys; print(sys.executable)') -CaptureOutput
        if ($resolved.ExitCode -eq 0) {
            $candidate = $resolved.Output.Split("`n")[0].Trim()
            if (Test-ArcPythonVersion -Path $candidate) {
                return [System.IO.Path]::GetFullPath($candidate)
            }
        }
    }
    return $null
}

function Invoke-ArcDownload {
    param([string] $Url, [string] $Partial)

    Remove-Item -LiteralPath $Partial -Force -ErrorAction SilentlyContinue
    $iwrParams = @{ Uri = $Url; OutFile = $Partial; TimeoutSec = 300; ErrorAction = 'Stop' }
    if ($PSVersionTable.PSVersion.Major -lt 6) {
        $iwrParams['UseBasicParsing'] = $true
    }
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            Invoke-WebRequest @iwrParams | Out-Null
            return
        } catch {
            Remove-Item -LiteralPath $Partial -Force -ErrorAction SilentlyContinue
            if ($attempt -eq 3) {
                throw
            }
            Write-ArcDiag ('  -> download attempt ' + $attempt + ' failed; retrying')
        }
    }
}

function Get-ArcBootstrapPython {
    param([string] $InstallHome, [string] $RepoRoot, [string] $Key)

    $spec = Get-ArcManifestAsset -ManifestPath (Join-Path $RepoRoot 'config/dependencies.json') -Key $Key
    Assert-ArcHostTools
    Assert-ArcFreeSpace -Path $InstallHome -ArchiveSize $spec.Size

    $cacheDir = Join-Path $InstallHome 'cache\python'
    $runtimeDir = Join-Path $InstallHome 'runtime'
    $archive = Join-Path $cacheDir $spec.Asset
    $partial = $archive + '.part'
    if (-not (Test-Path -LiteralPath $cacheDir -PathType Container)) {
        New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null
    }
    if (-not (Test-Path -LiteralPath $runtimeDir -PathType Container)) {
        New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    }

    $verified = $false
    if (Test-Path -LiteralPath $archive -PathType Leaf) {
        $actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
        $verified = [string]::Equals($actualHash, $spec.Sha256, [System.StringComparison]::OrdinalIgnoreCase)
        if (-not $verified) {
            Write-ArcDiag '  -> discarding cached archive with SHA-256 mismatch'
            Remove-Item -LiteralPath $archive -Force
        }
    }
    if (-not $verified) {
        Write-ArcDiag ('  -> downloading pinned CPython: ' + $spec.Asset)
        Invoke-ArcDownload -Url $spec.Url -Partial $partial
        $actualHash = (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash
        if (-not [string]::Equals($actualHash, $spec.Sha256, [System.StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
            throw ('ERROR: SHA-256 mismatch for ' + $spec.Asset + ' (expected ' + $spec.Sha256 + ', got ' + $actualHash + ')')
        }
        Move-Item -LiteralPath $partial -Destination $archive -Force
        Write-ArcDiag ('  -> SHA-256 verified: ' + $spec.Sha256)
    }

    $actualSize = (Get-Item -LiteralPath $archive).Length
    if ($actualSize -ne $spec.Size) {
        Remove-Item -LiteralPath $archive -Force
        throw ('ERROR: archive size mismatch for ' + $spec.Asset + ' (expected ' + $spec.Size + ' bytes, got ' + $actualSize + ')')
    }

    $rootName = ($spec.PythonPath -split '[/\\]')[0]
    $pythonDir = Join-Path $runtimeDir $rootName
    if (Test-Path -LiteralPath $pythonDir -PathType Container) {
        Remove-Item -LiteralPath $pythonDir -Recurse -Force
    }
    Write-ArcDiag ('  -> extracting into ' + $runtimeDir)
    $result = Invoke-ArcNative -FilePath 'tar.exe' -Arguments @('-xzf', $archive, '-C', $runtimeDir)
    if ($result.ExitCode -ne 0) {
        throw 'ERROR: tar.exe failed to extract the pinned CPython archive'
    }

    $pythonExe = Join-Path $runtimeDir ($spec.PythonPath.Replace('/', '\'))
    if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
        throw ('ERROR: bootstrapped interpreter not found: ' + $pythonExe)
    }
    if (-not (Test-ArcPythonVersion -Path $pythonExe)) {
        throw ('ERROR: bootstrapped interpreter failed the Python >=' + $ArcPythonMinMajor + '.' + $ArcPythonMinMinor + ' check: ' + $pythonExe)
    }
    Write-ArcDiag ('  -> verified ' + (Get-ArcVersionText -Path $pythonExe))
    return [System.IO.Path]::GetFullPath($pythonExe)
}

function Write-ArcWriteFreeCheck {
    param([string] $InstallHome, [string] $RepoRoot, [string] $Key)

    # Read-only: no download, no extraction, no directory or file creation.
    $spec = Get-ArcManifestAsset -ManifestPath (Join-Path $RepoRoot 'config/dependencies.json') -Key $Key
    $pythonExe = Join-Path (Join-Path $InstallHome 'runtime') ($spec.PythonPath.Replace('/', '\'))
    if (Test-ArcPythonVersion -Path $pythonExe) {
        $version = Get-ArcVersionText -Path $pythonExe
        Write-Output ("[{0,20}] Python bootstrap: {1} ({2})" -f 'Verified', $pythonExe, $version)
        return
    }
    Write-Output ("[{0,20}] Python bootstrap: no local Python >=3.12; re-run without -Check to download pinned CPython ({1})" -f 'Needs manual action', $spec.Asset)
}

if ($ExecuteBootstrap) {
    $repo = $BootstrapRepo
    if ([string]::IsNullOrWhiteSpace($repo)) {
        $repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
    }
    $homeDir = $BootstrapHome
    if ([string]::IsNullOrWhiteSpace($homeDir)) {
        $homeDir = Join-Path $HOME '.arcanum'
    }
    $key = $BootstrapKey
    if ([string]::IsNullOrWhiteSpace($key)) {
        $key = Get-ArcPlatformAssetKey
    }
    Assert-ArcFullInstallSupport -ManifestPath (Join-Path $repo 'config/dependencies.json') -Key $key
    if ($BootstrapCheck) {
        Write-ArcWriteFreeCheck -InstallHome $homeDir -RepoRoot $repo -Key $key
    } else {
        Get-ArcBootstrapPython -InstallHome $homeDir -RepoRoot $repo -Key $key
    }
}

"""Static checks for the Windows Python bootstrap.

Windows PowerShell 5.1 is the hard baseline because the documented entry point
uses ``powershell -File ...``.  CI parses the scripts with Windows PowerShell;
these local tests enforce the source-level constraints that keep the bootstrap
5.1-compatible, write-free under ``-Check``, and safe around the pinned URL and
SHA-256.  No PowerShell interpreter is required to run them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_PS1 = ROOT / "installers" / "bootstrap" / "bootstrap.ps1"
INSTALL_PS1 = ROOT / "installers" / "install.ps1"
MANIFEST = ROOT / "config" / "dependencies.json"


def read(path: Path) -> str:
    assert path.is_file(), f"{path.relative_to(ROOT)} is missing"
    return path.read_text(encoding="utf-8")


def function_body(text: str, name: str) -> str:
    """Return the body of a top-level PowerShell function, without its braces."""
    match = re.search(rf"^function {re.escape(name)} \{{\n(.*?)^\}}\n", text, re.S | re.M)
    assert match, f"function {name} not found in bootstrap.ps1"
    return match.group(1)


def check_only_branch(text: str) -> str:
    assert "# BEGIN CHECK-ONLY BRANCH" in text, "install.ps1 needs a marked check-only branch"
    assert "# END CHECK-ONLY BRANCH" in text, "install.ps1 needs a marked check-only branch end"
    return text[
        text.index("# BEGIN CHECK-ONLY BRANCH") : text.index("# END CHECK-ONLY BRANCH")
    ]


def test_bootstrap_script_exists():
    assert BOOTSTRAP_PS1.is_file(), "installers/bootstrap/bootstrap.ps1 is missing"


def test_bootstrap_has_no_powershell7_only_syntax():
    text = read(BOOTSTRAP_PS1)
    for token in (
        "??",
        "?.",
        "&&",
        "||",
        "-AsHashtable",
        "::new(",
        "ForEach-Object -Parallel",
        "$IsWindows",
        "Test-Json",
    ):
        assert token not in text, f"PowerShell 7-only syntax {token!r} found in bootstrap.ps1"
    assert not re.search(r"(?<![\w$])\?(?!\w)", text), "ternary / '?' alias operator found"
    for path in (BOOTSTRAP_PS1, INSTALL_PS1):
        assert "?" not in read(path), f"{path.relative_to(ROOT)} contains a '?' operator"


def test_bootstrap_uses_powershell51_primitives():
    text = read(BOOTSTRAP_PS1)
    for token in (
        "Invoke-WebRequest",
        "Get-FileHash",
        "tar.exe",
        "-xzf",
        "ConvertFrom-Json",
        "PROCESSOR_ARCHITECTURE",
        "--version",
        "UseBasicParsing",
    ):
        assert token in text, f"bootstrap.ps1 must use {token!r}"
    assert "Expand-Archive" not in text, "Expand-Archive cannot extract .tar.gz on 5.1"


def test_manifest_urls_keep_the_literal_plus():
    text = read(BOOTSTRAP_PS1)
    assert "%2B" not in text, "URLs must not be percent-encoded"
    assert "EscapeDataString" not in text, "URLs must be fetched verbatim"
    assert ".Replace('+'" not in text, "literal '+' must not be rewritten"
    assert "Uri = $Url" in text, "Invoke-WebRequest must receive the manifest URL unchanged"

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assets = manifest["python"]["bootstrap"]["assets"]
    for key in ("windows-x64", "windows-arm64"):
        url = assets[key]["url"]
        assert "+" in url and "%2B" not in url and " " not in url
        assert url.endswith(assets[key]["asset"])
        assert assets[key]["python_path"] == "python/python.exe"


def test_hash_and_size_are_verified_before_extracting():
    body = function_body(read(BOOTSTRAP_PS1), "Get-ArcBootstrapPython")
    assert "Get-FileHash" in body and "-Algorithm SHA256" in body
    assert body.index("Get-FileHash") < body.index("tar.exe"), "verify before extracting"
    assert "Sha256" in body, "hash must be compared with the pinned manifest value"
    assert "Size" in body, "compressed byte count must be checked against the manifest"


def test_free_space_is_checked_before_downloading():
    text = read(BOOTSTRAP_PS1)
    body = function_body(text, "Get-ArcBootstrapPython")
    assert "Assert-ArcFreeSpace" in body
    assert body.index("Assert-ArcFreeSpace") < body.index("Invoke-ArcDownload")
    assert "AvailableFreeSpace" in function_body(text, "Get-ArcFreeSpaceBytes")


def test_windows_arm64_unsupported_is_reported_after_manifest_check():
    body = function_body(read(BOOTSTRAP_PS1), "Assert-ArcFullInstallSupport")
    assert "Get-ArcManifestAsset" in body
    assert body.index("Get-ArcManifestAsset") < body.index("windows-arm64")
    assert "Maestro" in body, "the unsupported reason must name the missing Maestro artifact"
    for forbidden in ("Invoke-WebRequest", "tar.exe", "New-Item", "Move-Item"):
        assert forbidden not in body, "ARM64 gate must run before any Python bootstrap"


def test_bootstrapped_interpreter_version_is_verified():
    text = read(BOOTSTRAP_PS1)
    body = function_body(text, "Get-ArcBootstrapPython")
    assert "Test-ArcPythonVersion" in body
    assert "Get-ArcVersionText" in body
    assert "version_info" in text and "--version" in text
    assert "python.exe" in text, "the Windows interpreter path must come from the manifest"


def test_write_free_check_helper_never_downloads_extracts_or_writes():
    body = function_body(read(BOOTSTRAP_PS1), "Write-ArcWriteFreeCheck")
    for forbidden in (
        "Invoke-WebRequest",
        "Get-FileHash",
        "tar.exe",
        "New-Item",
        "Remove-Item",
        "Move-Item",
        "Set-Content",
        "Copy-Item",
        "Expand-Archive",
        "Get-ArcBootstrapPython",
        "Invoke-ArcDownload",
        "Assert-ArcFreeSpace",
    ):
        assert forbidden not in body, f"-Check helper must not reference {forbidden}"
    assert "Python bootstrap" in body
    assert "Needs manual action" in body


def test_install_script_calls_bootstrap_before_the_engine():
    text = read(INSTALL_PS1)
    source = text.index(". $BootstrapScript")
    key = text.index("Get-ArcPlatformAssetKey")
    support = text.index("Assert-ArcFullInstallSupport")
    host = text.index("Get-ArcHostPython")
    bootstrap = text.index("Get-ArcBootstrapPython")
    engine = text.index("& $BootstrapPython $Core")
    assert source < key < support < host < bootstrap < engine
    assert "ARCANUM_BOOTSTRAP_PYTHON" in text, "engine must learn the bootstrapped interpreter"


def test_install_check_only_branch_is_write_free():
    branch = check_only_branch(read(INSTALL_PS1))
    assert "Write-ArcWriteFreeCheck" in branch
    assert "Unsupported" in branch, "ARM64 must be reported by -Check too"
    for forbidden in (
        "Invoke-WebRequest",
        "Get-FileHash",
        "tar.exe",
        "Get-ArcBootstrapPython",
        "Invoke-ArcDownload",
        "New-Item",
        "Remove-Item",
        "Move-Item",
        "Set-Content",
    ):
        assert forbidden not in branch, f"check-only branch must not reference {forbidden}"
    assert "--python $HostPython" in branch, "check branch must pin the detected interpreter"


def test_install_script_normalizes_powershell_flags_for_the_engine():
    text = read(INSTALL_PS1)
    assert "'--check'" in text and "'--non-interactive'" in text

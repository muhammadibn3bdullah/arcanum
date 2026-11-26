"""Contract test for the zero-prerequisite bootstrap CI and docs.

CI must exercise every new bootstrap surface (pinned manifest, bash syntax,
Windows PowerShell 5.1 parser, write-free ``--check``, the five-skill inventory
and the source checksum manifest) without publishing a release. The docs must
describe the user-facing zero-prerequisite flow honestly: no Python
prerequisite, the PowerShell 5.1 path, a supported-platform matrix, a manual
fallback, and provider authentication kept separate from installation.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
README = ROOT / "README.md"
INSTALLERS_README = ROOT / "installers" / "README.md"
INSTALL_DOCS = ROOT / "docs" / "installation"

# Each anchor must be carried by a named CI step so a regression is obvious.
REQUIRED_STEP_ANCHORS = (
    "bootstrap manifest",
    "bash syntax",
    "PowerShell parser",
    "--check write-free",
    "five-skill inventory",
    "checksums.txt",
)


def ci_text() -> str:
    return CI.read_text(encoding="utf-8")


def step_names(text: str) -> list[str]:
    return [
        match.group(1).strip()
        for match in re.finditer(r"^\s*-?\s*name:\s*(.+?)\s*$", text, re.MULTILINE)
    ]


# --- CI contract -------------------------------------------------------------


def test_ci_declares_all_bootstrap_contract_steps():
    names = " | ".join(step_names(ci_text())).lower()
    for anchor in REQUIRED_STEP_ANCHORS:
        assert anchor.lower() in names, f"CI has no step named for {anchor!r}"


def test_ci_matrix_covers_supported_runners():
    text = ci_text()
    for runner in ("ubuntu-latest", "macos-latest", "windows-latest"):
        assert runner in text, f"CI matrix is missing {runner}"


def test_ci_does_not_publish_releases():
    text = ci_text().lower()
    for forbidden in ("action-gh-release", "gh release create", "gh release edit", "softprops"):
        assert forbidden not in text, f"CI must not publish releases; found {forbidden!r}"
    assert "permissions:" in text
    assert "contents: read" in text


def test_ci_validates_bootstrap_manifest_pins():
    text = ci_text()
    assert "config/dependencies.json" in text
    assert "bootstrap" in text.lower()
    assert "sha256" in text.lower()


def test_ci_parses_powershell_scripts_with_windows_powershell():
    text = ci_text()
    assert "windows-latest" in text
    assert "ParseFile" in text
    assert "install.ps1" in text
    assert "bootstrap.ps1" in text


# --- Documentation contract --------------------------------------------------


def test_readme_documents_zero_prerequisite_flow():
    text = README.read_text(encoding="utf-8").lower()
    assert "no python prerequisite" in text
    assert "powershell 5.1" in text
    assert "windows arm64" in text
    assert "opencode auth login" in text
    assert "88" in text
    assert "namespaced" in text


def test_installers_readme_documents_bootstrap_and_manual_fallback():
    text = INSTALLERS_README.read_text(encoding="utf-8").lower()
    assert "bootstrap" in text
    assert "powershell 5.1" in text
    assert "windows arm64" in text
    assert "opencode auth login" in text
    assert "manual" in text
    assert "write-free" in text or "writes nothing" in text


def test_installation_guides_mention_bootstrap_and_auth_separation():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(INSTALL_DOCS.glob("*.md"))
    ).lower()
    assert "bootstrap" in combined
    assert "powershell 5.1" in combined
    assert "windows arm64" in combined
    assert "opencode auth login" in combined
    assert "manual" in combined

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "config" / "dependencies.json"

RELEASE = "20261124"
VERSION = "3.12.16"
BASE_URL = f"https://github.com/astral-sh/python-build-standalone/releases/download/{RELEASE}"

EXPECTED_ASSETS = {
    "linux-x64": {
        "asset": f"cpython-{VERSION}+{RELEASE}-x86_64-unknown-linux-gnu-install_only.tar.gz",
        "sha256": "6774c7f49ee21c1723f7ac91961a5bf9f3ee3ae6e4d82ff58bd358958b546461",
        "python_path": "python/bin/python3",
    },
    "linux-arm64": {
        "asset": f"cpython-{VERSION}+{RELEASE}-aarch64-unknown-linux-gnu-install_only.tar.gz",
        "sha256": "2e6b3576f96d5b8861e4c265ea6db3d19eb24027bbd6c22f044544989444b20b",
        "python_path": "python/bin/python3",
    },
    "darwin-x64": {
        "asset": f"cpython-{VERSION}+{RELEASE}-x86_64-apple-darwin-install_only.tar.gz",
        "sha256": "ccce24cfd3f56f62561d2c9480fcc567ca3a9bac68450cd73e11aeb631befa1f",
        "python_path": "python/bin/python3",
    },
    "darwin-arm64": {
        "asset": f"cpython-{VERSION}+{RELEASE}-aarch64-apple-darwin-install_only.tar.gz",
        "sha256": "431b99a9c2cdd37ad9b06e425be0e520d2dbce0552abe454257abef592fe3634",
        "python_path": "python/bin/python3",
    },
    "windows-x64": {
        "asset": f"cpython-{VERSION}+{RELEASE}-x86_64-pc-windows-msvc-install_only.tar.gz",
        "sha256": "8152ff5b6c6c044ddbbaa3531dda59cb9ce5bd6cb670d4c9ac2e3ab91afa8778",
        "python_path": "python/python.exe",
    },
    "windows-arm64": {
        "asset": f"cpython-{VERSION}+{RELEASE}-aarch64-pc-windows-msvc-install_only.tar.gz",
        "sha256": "f1c1944f9eb00be38022fcb64fcf7b985e482c0adea3071e06f4201206e1e480",
        "python_path": "python/python.exe",
    },
}


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def bootstrap_section() -> dict:
    manifest = load_manifest()
    assert "python" in manifest, "manifest has no python section"
    assert "bootstrap" in manifest["python"], "manifest has no python.bootstrap section"
    return manifest["python"]["bootstrap"]


def test_bootstrap_release_and_version_are_pinned():
    bootstrap = bootstrap_section()
    assert bootstrap["repository"] == "astral-sh/python-build-standalone"
    assert bootstrap["release"] == RELEASE
    assert bootstrap["version"] == VERSION


def test_bootstrap_declares_all_six_platforms():
    assets = bootstrap_section()["assets"]
    assert set(assets) == set(EXPECTED_ASSETS)


def test_bootstrap_assets_match_pinned_checksums_and_paths():
    assets = bootstrap_section()["assets"]
    for platform, expected in EXPECTED_ASSETS.items():
        entry = assets[platform]
        assert entry["asset"] == expected["asset"], platform
        assert entry["url"] == f"{BASE_URL}/{expected['asset']}", platform
        assert entry["sha256"] == expected["sha256"], platform
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), platform
        assert isinstance(entry["size"], int) and not isinstance(entry["size"], bool), platform
        assert entry["size"] > 0, platform
        assert entry["python_path"] == expected["python_path"], platform


def test_bootstrap_checksums_are_unique():
    assets = bootstrap_section()["assets"]
    digests = [entry["sha256"] for entry in assets.values()]
    assert len(digests) == len(set(digests))

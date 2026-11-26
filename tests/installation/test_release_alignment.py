"""Release-alignment tests for the v0.1.1 source manifest and tag/HEAD agreement.

The release workflow consumes two local guarantees before anything is tagged or
published:

1. ``checksums.txt`` is a SHA-256 manifest of every git-tracked source file,
   in the exact row format the CI checker tolerates (``<64-hex>  <path>``).
2. A helper compares a published release tag's target commit SHA with the
   locally tested ``git rev-parse HEAD``; a mismatch must never be published.

These tests run entirely offline -- the GitHub API cross-check is performed by
the release operator, using :func:`release_tag_matches` on the API-provided SHA.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "scripts" / "generate_checksums.py"
MANIFEST = ROOT / "checksums.txt"

# Mirrors the tolerant CI regex in .github/workflows/ci.yml ("Source checksum
# manifest" step): single/double space or binary '*' are both accepted.
CI_ROW = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$")


def load_generator():
    spec = importlib.util.spec_from_file_location("generate_checksums", GENERATOR)
    assert spec is not None and spec.loader is not None, GENERATOR
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tracked_source_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    )
    names = {part.decode("utf-8") for part in result.stdout.split(b"\0") if part}
    return names - {"checksums.txt"}


def manifest_text() -> str:
    return MANIFEST.read_text(encoding="utf-8")


# --- checksum manifest --------------------------------------------------------


def test_checksums_manifest_exists():
    assert MANIFEST.is_file(), "checksums.txt missing; run scripts/generate_checksums.py"


def test_checksums_manifest_covers_every_tracked_source_file():
    gen = load_generator()
    rows = gen.parse_rows(manifest_text())
    expected = tracked_source_files()
    assert expected, "git reports no tracked files"
    assert expected <= set(rows), f"missing checksums: {sorted(expected - set(rows))[:10]}"
    assert set(rows) <= expected, f"stale checksum rows: {sorted(set(rows) - expected)[:10]}"


def test_checksums_digests_match_current_files():
    gen = load_generator()
    ok, errors = gen.verify_manifest(ROOT)
    assert ok, errors


def test_every_manifest_row_matches_ci_tolerance():
    lines = [line for line in manifest_text().splitlines() if line.strip()]
    assert lines, "checksums.txt is empty"
    for line in lines:
        assert CI_ROW.match(line.strip()), f"unparseable checksum row: {line!r}"


# --- release tag / tested commit alignment ------------------------------------


def test_release_tag_helper_matches_head_only_for_exact_sha():
    gen = load_generator()
    head = gen.git_head(ROOT)
    assert re.fullmatch(r"[0-9a-f]{40}", head), head
    assert gen.release_tag_matches(ROOT, head) is True
    assert gen.release_tag_matches(ROOT, head.upper()) is True  # case-insensitive
    other = "0" * 40 if head != "0" * 40 else "1" * 40
    assert gen.release_tag_matches(ROOT, other) is False


def test_release_tag_helper_rejects_non_commit_sha():
    gen = load_generator()
    for value in ("v0.1.1", "deadbeef", "", "z" * 40):
        with pytest.raises(ValueError):
            gen.release_tag_matches(ROOT, value)

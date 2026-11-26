#!/usr/bin/env python3
"""Generate and verify the SHA-256 manifest for git-tracked source files.

Usage (from the repository root)::

    python scripts/generate_checksums.py            # (re)write checksums.txt
    python scripts/generate_checksums.py --check    # verify, write nothing

The manifest is one ``<64-hex digest>  <path>`` row per tracked file, sorted by
path, with ``checksums.txt`` itself excluded (it cannot contain its own hash).
The row format is intentionally compatible with the tolerant CI checker in
``.github/workflows/ci.yml``: one or more whitespace characters separate the
digest from the path, and an optional binary-mode ``*`` is accepted.

The module also exposes the local half of the release-alignment check:
``git_head`` returns the tested commit SHA and ``release_tag_matches`` compares
a GitHub API tag-target SHA against it. No network access and no writes happen
on import or when ``--check`` is used.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "checksums.txt"
# A manifest cannot hash itself; every other tracked file is included.
EXCLUDED = frozenset({MANIFEST_NAME})

CI_ROW = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$")
GIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")


def tracked_files(root: Path = ROOT) -> list[str]:
    """Return sorted POSIX-style paths of all tracked files except our manifest."""
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
    )
    names = (part.decode("utf-8") for part in result.stdout.split(b"\0") if part)
    return sorted(name for name in names if name not in EXCLUDED)


def sha256_file(path: Path) -> str:
    """Stream a file through SHA-256 without loading it entirely into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_rows(root: Path = ROOT) -> list[str]:
    """Build manifest rows for the current tree."""
    return [f"{sha256_file(root / name)}  {name}" for name in tracked_files(root)]


def parse_rows(text: str) -> dict[str, str]:
    """Parse accepted CI row formats into ``{path: lowercase_digest}``."""
    rows: dict[str, str] = {}
    for line in text.splitlines():
        match = CI_ROW.match(line.strip())
        if match:
            rows[match.group(2)] = match.group(1).lower()
    return rows


def write_manifest(root: Path = ROOT, output: Path | str | None = None) -> Path:
    """Write the manifest to ``output`` (default: repo-root checksums.txt)."""
    destination = Path(output) if output is not None else root / MANIFEST_NAME
    rows = build_rows(root)
    destination.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    return destination


def verify_manifest(
    root: Path = ROOT, path: Path | str | None = None
) -> tuple[bool, list[str]]:
    """Verify the manifest against the working tree without writing anything."""
    destination = Path(path) if path is not None else root / MANIFEST_NAME
    if not destination.is_file():
        return False, [f"missing manifest: {destination}"]

    rows = parse_rows(destination.read_text(encoding="utf-8"))
    expected = set(tracked_files(root))
    errors: list[str] = []

    missing = sorted(expected - set(rows))
    if missing:
        errors.append(f"missing checksum rows: {missing[:10]}")
    stale = sorted(set(rows) - expected)
    if stale:
        errors.append(f"stale checksum rows: {stale[:10]}")
    for name, digest in sorted(rows.items()):
        file_path = root / name
        if file_path.is_file():
            actual = sha256_file(file_path)
            if actual != digest:
                errors.append(f"checksum mismatch for {name}: {digest} != {actual}")

    return not errors, errors


def git_head(root: Path = ROOT) -> str:
    """Return the lowercase 40-hex commit SHA of local HEAD."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    )
    head = result.stdout.strip().lower()
    if not GIT_SHA.fullmatch(head):
        raise ValueError(f"git rev-parse HEAD did not return a commit SHA: {head!r}")
    return head


def release_tag_matches(root: Path = ROOT, tag_target_sha: str = "") -> bool:
    """Compare a release tag's API target SHA with the locally tested HEAD.

    ``tag_target_sha`` is the commit SHA GitHub reports for the release tag
    (``GET /repos/{owner}/{repo}/git/ref/tags/{tag}`` dereferenced to a commit).
    Invalid input raises ``ValueError`` so a malformed release can never be
    mistaken for a match.
    """
    candidate = str(tag_target_sha).strip().lower()
    if not GIT_SHA.fullmatch(candidate):
        raise ValueError(f"release tag target is not a 40-hex commit SHA: {tag_target_sha!r}")
    return candidate == git_head(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true", help="verify checksums.txt without writing"
    )
    parser.add_argument(
        "--output", default=None, help="alternate manifest path (relative to repo root)"
    )
    args = parser.parse_args(argv)

    output: Path | str | None = args.output
    if output is not None and not Path(output).is_absolute():
        output = ROOT / output

    if args.check:
        ok, errors = verify_manifest(ROOT, output)
        if not ok:
            for error in errors:
                print(error)
            return 1
        print(f"checksums.txt OK: {len(tracked_files(ROOT))} tracked digests verified")
        return 0

    destination = write_manifest(ROOT, output)
    print(f"wrote {len(tracked_files(ROOT))} digests to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

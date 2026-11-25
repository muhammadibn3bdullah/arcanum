#!/usr/bin/env python3
"""Fail if obvious credentials are present in tracked source files."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git", "node_modules", "dist", "__pycache__", "logs", ".venv", "venv",
    # Vendored third-party skills intentionally contain detection patterns and examples.
    "vendor", "licenses",
}
PATTERNS = [
    ("discord_token", re.compile(r"MT[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,}")),
    ("github_token", re.compile(r"ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{30,}")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]
ALLOW = {".env.example"}


def main() -> int:
    hits = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(p in SKIP_DIRS for p in path.parts):
            continue
        if path.relative_to(ROOT).as_posix() in ALLOW:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, pattern in PATTERNS:
            if pattern.search(text):
                hits.append(f"{path.relative_to(ROOT)}: {name}")
    if hits:
        print("\n".join(hits))
        return 1
    print("secret scan OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

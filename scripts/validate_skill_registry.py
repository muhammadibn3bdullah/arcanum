#!/usr/bin/env python3
"""Validate that the skill registry matches the bundled files."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "skill_id", "name", "source", "source_repository", "source_revision",
    "content_hash", "version", "author", "license", "attribution",
    "description", "triggers", "supported_agents", "required_tools",
    "required_dependencies", "references", "local_modifications",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    registry = json.loads((ROOT / "skills/registry/registry.json").read_text())
    errors = []
    seen = set()
    for entry in registry.get("skills", []):
        missing = REQUIRED - set(entry)
        if missing:
            errors.append(f"{entry.get('skill_id')}: missing {sorted(missing)}")
        skill_root = ROOT / "skills" / ("arcanum" if entry["source"] == "arcanum" else "vendor/claude-bughunter") / entry["skill_id"]
        if not skill_root.is_dir():
            errors.append(f"{entry['skill_id']}: directory missing")
            continue
        if entry["skill_id"] in seen:
            errors.append(f"{entry['skill_id']}: duplicate registry entry")
        seen.add(entry["skill_id"])
        files = sorted(p for p in skill_root.rglob("*") if p.is_file())
        h = hashlib.sha256()
        for file in files:
            h.update(file.relative_to(skill_root).as_posix().encode())
            h.update(sha256(file).encode())
        if h.hexdigest() != entry["content_hash"]:
            errors.append(f"{entry['skill_id']}: content hash mismatch")
    disk = {
        p.parent.name
        for folder in ("skills/arcanum", "skills/vendor/claude-bughunter")
        for p in (ROOT / folder).glob("*/SKILL.md")
    }
    if disk - seen:
        errors.append(f"unregistered skills: {sorted(disk - seen)}")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"registry OK: {len(seen)} skills")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

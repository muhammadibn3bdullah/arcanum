#!/usr/bin/env python3
"""Generate the authoritative Arcanum skill registry from actual files."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = "https://github.com/elementalsouls/Claude-BugHunter"
UPSTREAM_REV = "6a530fb3149c8c34d3ba0eb31b4eb431e91da648"
CUSTOM_COMMIT = "d9031de"
DATE_IMPORTED = "2026-11-25"

CUSTOM = {
    "api-fingerprint-caido": ["caido_read", "http_read"],
    "interceptor": ["browser_control", "screenshot"],
    "js_analysis": ["source_read", "grep"],
    "lazy_chunk_downloader": ["source_read", "http_read"],
    "say_hello": [],
}
AGENTS = ["hackbot-lead", "recon-agent", "hunt-agent", "validate-agent", "report-agent"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block.replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
    return h.hexdigest()


def frontmatter(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not match:
        return out
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            out[key.strip()] = value.strip().strip('"\'')
    return out


def skill_entry(path: Path, source_kind: str) -> dict:
    skill_md = path / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    meta = frontmatter(text)
    files = sorted(p for p in path.rglob("*") if p.is_file())
    tree_hash = hashlib.sha256()
    for file in files:
        tree_hash.update(file.relative_to(path).as_posix().encode())
        tree_hash.update(sha256(file).encode())
    description = meta.get("description", "")
    if source_kind == "arcanum":
        source = "arcanum"
        revision = CUSTOM_COMMIT
        author = "Arcanum contributors"
        license_name = "PENDING"
        attribution = "Arcanum-authored skill"
        modifications = "Original Arcanum skill"
    else:
        source = "claude-bughunter"
        revision = UPSTREAM_REV
        author = "Claude BugHunter contributors"
        license_name = "CC-BY-4.0 (content); MIT (code)"
        attribution = f"Imported from {UPSTREAM} at {revision}"
        modifications = "Unmodified vendored copy" if not (path / "LOCAL_MODIFICATIONS.md").exists() else "See LOCAL_MODIFICATIONS.md"
    return {
        "skill_id": path.name,
        "name": meta.get("name", path.name),
        "source": source,
        "source_repository": "" if source_kind == "arcanum" else UPSTREAM,
        "source_revision": revision,
        "content_hash": tree_hash.hexdigest(),
        "version": meta.get("version", "0.1.0"),
        "author": author,
        "license": license_name,
        "attribution": attribution,
        "description": description,
        "triggers": [t.strip() for t in meta.get("description", "").split(",") if t.strip()][:10],
        "supported_agents": AGENTS,
        "required_tools": CUSTOM.get(path.name, []) if source_kind == "arcanum" else [],
        "required_dependencies": [],
        "references": [p.relative_to(path).as_posix() for p in files if p != skill_md],
        "local_modifications": modifications,
        "import_date": DATE_IMPORTED if source_kind == "vendor" else "",
    }


def main() -> None:
    entries = []
    for source_kind, folder in (("arcanum", ROOT / "skills/arcanum"), ("vendor", ROOT / "skills/vendor/claude-bughunter")):
        for skill_md in sorted(folder.glob("*/SKILL.md")):
            entries.append(skill_entry(skill_md.parent, source_kind))
    registry = {
        "schema_version": 1,
        "generated_on": date.today().isoformat(),
        "generator": "scripts/generate_skill_registry.py",
        "source_of_truth": "files in skills/arcanum and skills/vendor/claude-bughunter",
        "upstream": {
            "repository": UPSTREAM,
            "revision": UPSTREAM_REV,
            "import_date": DATE_IMPORTED,
            "license_code": "MIT",
            "license_content": "CC-BY-4.0",
        },
        "skill_count": len(entries),
        "skills": entries,
    }
    out = ROOT / "skills/registry/registry.json"
    out.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(entries)} skills)")


if __name__ == "__main__":
    main()

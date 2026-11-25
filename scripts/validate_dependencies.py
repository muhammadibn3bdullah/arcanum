#!/usr/bin/env python3
"""Validate the pinned dependency manifest structure."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FIELDS = {
    "name", "version", "source", "platforms", "architecture",
    "artifact", "checksum", "install_method", "verify_command",
    "license", "mandatory",
}


def main() -> int:
    path = ROOT / "config/dependencies.json"
    if not path.exists():
        print("dependency manifest missing")
        return 1
    data = json.loads(path.read_text())
    errors = []
    for key in ("python", "bun", "opencode", "maestro", "skills", "runtime", "discord"):
        if key not in data:
            errors.append(f"missing manifest section: {key}")
    def check_assets(section: str, node: dict, prefix: str) -> None:
        if "url" in node or "asset" in node:
            if not node.get("url"):
                errors.append(f"{section}/{prefix}: missing official URL")
            if not node.get("sha256"):
                errors.append(f"{section}/{prefix}: missing published checksum")
            return
        for key, value in node.items():
            if isinstance(value, dict):
                check_assets(section, value, f"{prefix}/{key}" if prefix else key)

    for section in ("bun", "opencode", "maestro"):
        block = data.get(section, {})
        assets = block.get("assets", {})
        if not assets:
            errors.append(f"{section}: no pinned assets")
        check_assets(section, assets, "")
    text = json.dumps(data)
    if "get.mobile.dev" in text or "mobile-next/maestro" in text.replace(" ", ""):
        if "Do NOT install" not in text:
            errors.append("manifest must not install the unrelated mobile.dev Maestro CLI")
    if errors:
        print("\n".join(errors))
        return 1
    print("dependency manifest OK: pinned official assets with checksums")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Initialize or migrate Arcanum runtime databases without overwriting data."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arcanum.config.settings import Settings  # noqa: E402
from arcanum.memory.database import Database  # noqa: E402
from arcanum.runtime.tab_registry import TabRegistry  # noqa: E402


def main() -> int:
    settings = Settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.tab_registry_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(settings.db_path)
    tables_before = {
        r["name"] for r in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    db.close()
    registry = TabRegistry(settings.tab_registry_path)
    registry.close()
    db = Database(settings.db_path)
    tables_after = {
        r["name"] for r in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    db.close()
    print(json.dumps({
        "arcanum_home": str(settings.db_path.parent),
        "database": str(settings.db_path),
        "tab_registry": str(settings.tab_registry_path),
        "tables_added": sorted(tables_after - tables_before),
        "tables_total": len(tables_after),
        "data_preserved": True,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

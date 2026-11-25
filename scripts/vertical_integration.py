#!/usr/bin/env python3
"""Run one real Maestro/OpenCode specialist execution through the gate.

Usage:
    .venv/bin/python scripts/vertical_integration.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from arcanum.runtime import run_one_specialist  # noqa: E402


def main() -> int:
    result = run_one_specialist(
        agent="recon-agent",
        objective=(
            "Perform a structured reconnaissance-planning task for lab.local: "
            "identify likely attack surface categories and return them with evidence references."
        ),
        target_id="lab.local",
        authorized_target="lab.local",
        scope_ref="scope_lab_local",
    )
    print(json.dumps(result, indent=2))
    return 0 if result["decision"] == "ACCEPT" else 1


if __name__ == "__main__":
    raise SystemExit(main())

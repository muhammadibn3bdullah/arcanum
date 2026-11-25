#!/usr/bin/env python3
"""Run the full five-agent head-supervised cycle through real Maestro."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from arcanum.config.settings import Settings  # noqa: E402
from arcanum.runtime import run_five_agent_cycle  # noqa: E402


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "lab.local"
    result = run_five_agent_cycle(target, settings=Settings())
    summary = {
        "decision": result.get("decision"),
        "workflow_id": result.get("workflow_id"),
        "target_id": result.get("target_id"),
        "stage": result.get("stage"),
        "stages": {
            name: {
                "decision": stage.get("decision"),
                "state": stage.get("state"),
                "task_id": stage.get("task_id"),
                "execution_id": stage.get("execution_id"),
                "tab_id": stage.get("tab_id"),
                "callback_tab": stage.get("callback_tab"),
                "reasons": stage.get("reasons", []),
            }
            for name, stage in result.get("stages", {}).items()
        },
    }
    print(json.dumps(summary, indent=2))
    return 0 if result.get("decision") == "ACCEPT" else 1


if __name__ == "__main__":
    raise SystemExit(main())

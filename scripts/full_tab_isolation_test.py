#!/usr/bin/env python3
"""Real Maestro test: five-agent lifecycle across two isolated targets."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from arcanum.config.settings import Settings  # noqa: E402
from arcanum.memory.database import Database  # noqa: E402
from arcanum.runtime import (  # noqa: E402
    SPECIALISTS,
    TabRegistry,
    add_target_to_workflow,
    run_multi_target_cycle,
)


def main() -> int:
    settings = Settings()
    registry = TabRegistry(settings.tab_registry_path)
    targets = ["domain-a.com", "domain-b.com"]
    cycle = run_multi_target_cycle(
        targets, agents=SPECIALISTS, settings=settings,
        registry=registry, timeout=240,
    )
    workflow_id = cycle["workflow_id"]
    lead_tab_id = cycle["lead_tab_id"]

    mappings = registry.all_for_workflow(workflow_id)
    lead = [m for m in mappings if m["scope"] == "lead"]
    specialists = [m for m in mappings if m["scope"] == "specialist"]
    counts = registry.tab_counts_by_agent(workflow_id)

    db = Database(settings.db_path)
    rows = db.conn.execute(
        "SELECT execution_id, target_id, data FROM executions WHERE workflow_id=?",
        (workflow_id,),
    ).fetchall()
    executions = []
    for row in rows:
        data = json.loads(row["data"])
        executions.append({
            "execution_id": row["execution_id"],
            "target_id": row["target_id"],
            "decision": data["verification"]["decision"],
            "callback_tab": data.get("callback_tab"),
            "lead_tab_id": data.get("lead_tab_id"),
            "specialist_tab_id": data.get("specialist_tab_id"),
            "target_isolated": data["verification"]["checks"].get("structured_target_isolated"),
        })
    db.close()

    # Dynamic target addition must not create specialist tabs.
    before = registry.count_for_workflow(workflow_id)
    added = add_target_to_workflow("domain-c.com", workflow_id, registry, settings)
    after = registry.count_for_workflow(workflow_id)

    summary = {
        "decision": cycle["decision"],
        "workflow_id": workflow_id,
        "lead_tab_id": lead_tab_id,
        "lead_tab_count": len(lead),
        "specialist_tab_count": len(specialists),
        "total_tab_count": len(mappings),
        "tabs_by_agent": counts,
        "expected_total": 1 + 2 * len(SPECIALISTS),
        "execution_count": len(executions),
        "all_accepted": all(e["decision"] == "ACCEPT" for e in executions),
        "all_callbacks_on_lead": all(
            e["callback_tab"] == lead_tab_id for e in executions
        ),
        "all_target_isolated": all(e["target_isolated"] is True for e in executions),
        "distinct_tabs_per_agent": all(
            counts.get(agent, 0) == len(targets) for agent in SPECIALISTS
        ),
        "dynamic_target": {
            "scope_refs": added["scope_refs"],
            "tabs_before": before,
            "tabs_after": after,
            "no_new_tabs": before == after,
        },
        "executions": executions,
        "mappings": mappings,
    }
    print(json.dumps(summary, indent=2))

    ok = (
        cycle["decision"] == "ACCEPT"
        and summary["lead_tab_count"] == 1
        and summary["specialist_tab_count"] == 2 * len(SPECIALISTS)
        and summary["total_tab_count"] == summary["expected_total"]
        and summary["distinct_tabs_per_agent"]
        and summary["all_accepted"]
        and summary["all_callbacks_on_lead"]
        and summary["all_target_isolated"]
        and summary["dynamic_target"]["no_new_tabs"]
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

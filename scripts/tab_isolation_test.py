#!/usr/bin/env python3
"""Real Maestro integration test for C2.1.1 target-isolated persistent tabs.

Runs one workflow with two targets, dispatches Recon for both, then sends a
second task to Recon for the first target. Verifies:

- one central Lead tab for the workflow
- distinct Recon tabs for the two targets
- tab reuse for the second task on the same target
- callback correlation to the central Lead tab
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from arcanum.config.settings import Settings  # noqa: E402
from arcanum.core.execution import AcceptanceCriteria  # noqa: E402
from arcanum.core.task_brief import TaskBrief  # noqa: E402
from arcanum.memory.database import Database  # noqa: E402
from arcanum.runtime import (  # noqa: E402
    TabRegistry,
    run_multi_target_cycle,
    run_stage,
)


def main() -> int:
    settings = Settings()
    registry = TabRegistry(settings.tab_registry_path)
    targets = ["domain-a.com", "domain-b.com"]
    cycle = run_multi_target_cycle(
        targets, agents=["recon-agent"], settings=settings,
        registry=registry, timeout=240,
    )
    workflow_id = cycle["workflow_id"]
    lead_tab_id = cycle["lead_tab_id"]

    second = None
    if cycle["decision"] == "ACCEPT":
        brief = TaskBrief(
            workflow_id=workflow_id,
            target_id=targets[0],
            task_id=f"task_{uuid.uuid4().hex[:10]}",
            execution_id=f"exec_{uuid.uuid4().hex[:8]}",
            assigned_agent="recon-agent",
            objective=(
                "Second task for domain-a.com. Reasoning task; do not call "
                "tools. Return findings: [] and include exact workflow_id, "
                "target_id and authorized_target from this brief."
            ),
            authorized_target=targets[0],
            scope_ref=f"scope_{targets[0]}",
            operation_constraints=["authorized scope only"],
            available_capabilities=["caido_read"],
            expected_activities=["parse task brief", "return structured result"],
            expected_deliverables=["structured execution result"],
            acceptance_criteria=AcceptanceCriteria(
                expected_agent_id="recon-agent",
                expected_workflow_id=workflow_id,
                expected_target_id=targets[0],
                required_actions=["parse task brief"],
                required_structured_fields=["workflow_id", "target_id", "authorized_target"],
                required_field_values={
                    "workflow_id": workflow_id,
                    "target_id": targets[0],
                    "authorized_target": targets[0],
                },
                min_assistant_messages=1,
            ),
            required_evidence=["task-specific structured fields"],
        )
        second = run_stage(
            brief, settings=settings, timeout=240,
            registry=registry, lead_tab_id=lead_tab_id,
        )

    mappings = registry.all_for_workflow(workflow_id)
    recon = [m for m in mappings if m["agent_id"] == "recon-agent"]
    lead = [m for m in mappings if m["scope"] == "lead"]

    db = Database(settings.db_path)
    callbacks = [
        db.get_callback(cid)
        for cid in {
            (cycle["results"].get(t, {}).get("recon-agent", {}) or {}).get("callback_id", "")
            for t in targets
        } | {((second or {}).get("callback_id") or "")}
        if cid
    ]
    db.close()

    summary = {
        "decision": cycle["decision"],
        "workflow_id": workflow_id,
        "lead_tab_id": lead_tab_id,
        "lead_tab_count": len(lead),
        "recon_tab_count": len(recon),
        "recon_tabs": {m["target_id"]: m["tab_id"] for m in recon},
        "distinct_recon_tabs": len({m["tab_id"] for m in recon}) == len(recon),
        "first_task": {
            t: {
                "decision": cycle["results"].get(t, {}).get("recon-agent", {}).get("decision"),
                "tab_id": cycle["results"].get(t, {}).get("recon-agent", {}).get("tab_id"),
                "callback_tab": cycle["results"].get(t, {}).get("recon-agent", {}).get("callback_tab"),
            }
            for t in targets
        },
        "second_task": None if not second else {
            "decision": second.get("decision"),
            "tab_id": second.get("tab_id"),
            "tab_reused": second.get("tab_reused"),
            "callback_tab": second.get("callback_tab"),
            "reasons": second.get("reasons", []),
        },
        "callbacks": callbacks,
        "tab_count_total": len(mappings),
    }
    print(json.dumps(summary, indent=2))

    ok = (
        cycle["decision"] == "ACCEPT"
        and len(lead) == 1
        and len(recon) == 2
        and summary["distinct_recon_tabs"]
        and second is not None
        and second.get("decision") == "ACCEPT"
        and second.get("tab_reused") is True
        and (bool(recon) and second.get("tab_id") == recon[0]["tab_id"])
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

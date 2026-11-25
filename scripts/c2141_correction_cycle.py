#!/usr/bin/env python3
"""C2.1.4.1: real correction + clarification cycle inside one canonical room."""

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
from arcanum.core.models import ScopeRef  # noqa: E402
from arcanum.core.task_brief import TaskBrief  # noqa: E402
from arcanum.memory.database import Database  # noqa: E402
from arcanum.runtime import rooms, run_stage  # noqa: E402


def brief(wf: str, target: str, execution_id: str, objective: str, parent: str) -> TaskBrief:
    return TaskBrief(
        workflow_id=wf, target_id=target,
        task_id=f"task_{uuid.uuid4().hex[:10]}", execution_id=execution_id,
        parent_task_id=parent, assigned_agent="recon-agent", objective=objective,
        authorized_target=target, scope_ref="scope_c2141",
        operation_constraints=["authorized scope only", "sequential bounded execution"],
        available_capabilities=["caido_read"], expected_activities=["parse task brief", "return structured result"],
        expected_deliverables=["structured result"], required_evidence=["task-specific structured fields"],
        acceptance_criteria=AcceptanceCriteria(
            expected_agent_id="recon-agent", expected_workflow_id=wf, expected_target_id=target,
            required_actions=["parse task brief"],
            required_structured_fields=["workflow_id", "target_id", "authorized_target"],
            required_field_values={"workflow_id": wf, "target_id": target, "authorized_target": target},
            min_assistant_messages=1,
        ),
    )


def main() -> int:
    settings = Settings()
    wf = f"wf_correction_{uuid.uuid4().hex[:8]}"
    target = "SES_DED2"
    db = Database(settings.db_path)
    if not db.find_scopes_by_domain(target.lower()):
        db.save_scope(ScopeRef(scope_ref="scope_c2141", domain=target,
                               operator_id="local_operator", objective="authorized"))
    db.close()
    room = rooms.get_or_create_domain_room(wf, target, settings)
    before = rooms.room_messages(room.room_id, settings=settings)

    first = brief(wf, target, f"exec_{uuid.uuid4().hex[:8]}",
                  "Identify target and scope for SES_DED2. Reasoning only. Return findings: [].", "")
    r1 = run_stage(first, settings=settings, timeout=240)
    if r1.get("decision") != "ACCEPT":
        print(json.dumps({"decision": "BLOCKED", "stage": "initial", "result": r1}, indent=2))
        return 2

    second = brief(wf, target, f"exec_{uuid.uuid4().hex[:8]}",
                   "Correction for SES_DED2: restate target_id and scope_ref exactly from the original brief. Reasoning only.",
                   first.task_id)
    r2 = run_stage(second, settings=settings, timeout=240)
    if r2.get("decision") != "ACCEPT":
        print(json.dumps({"decision": "BLOCKED", "stage": "correction", "result": r2}, indent=2))
        return 3

    after = rooms.room_messages(room.room_id, settings=settings)
    new = after[len(before):]
    by_exec = {
        eid: [m for m in after if m.get("execution_id") == eid]
        for eid in (first.execution_id, second.execution_id)
    }
    summary = {
        "decision": "ACCEPT",
        "workflow_id": wf, "room_id": room.room_id,
        "first_execution": first.execution_id, "second_execution": second.execution_id,
        "different_execution_ids": first.execution_id != second.execution_id,
        "original_result_retained": any(
            m["execution_id"] == first.execution_id and m["message_type"] == "STRUCTURED_RESULT"
            for m in after
        ),
        "updated_result_present": any(
            m["execution_id"] == second.execution_id and m["message_type"] == "STRUCTURED_RESULT"
            for m in after
        ),
        "messages_by_execution": {k: [{"id": m["message_id"], "type": m["message_type"], "sender": m["sender_agent_id"]} for m in v] for k, v in by_exec.items()},
        "new_message_count": len(new),
        "room_count": len(Database(settings.db_path).list_rooms(wf)),
    }
    print(json.dumps(summary, indent=2))
    return 0 if summary["different_execution_ids"] and summary["room_count"] == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())

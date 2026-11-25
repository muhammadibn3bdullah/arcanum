#!/usr/bin/env python3
"""Controlled provider-unavailable/recovery test at the integration boundary.

This SIMULATES a provider failure. Real quota exhaustion was observed in the
five-agent lifecycle attempt and is reported separately.
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
from arcanum.core.execution import (  # noqa: E402
    AcceptanceCriteria, DispatchAcknowledgment, DispatchStatus,
)
from arcanum.core.models import ScopeRef  # noqa: E402
from arcanum.core.task_brief import TaskBrief  # noqa: E402
from arcanum.memory.database import Database  # noqa: E402
from arcanum.runtime import maestro, rooms, resume_stage, run_stage  # noqa: E402


def main() -> int:
    settings = Settings()
    wf = f"wf_quota_{uuid.uuid4().hex[:8]}"
    target = "SES_DED2"
    db = Database(settings.db_path)
    if not db.find_scopes_by_domain(target.lower()):
        db.save_scope(ScopeRef(scope_ref="scope_c2141", domain=target,
                               operator_id="local_operator", objective="authorized"))
    db.close()
    room = rooms.get_or_create_domain_room(wf, target, settings)
    execution_id = f"exec_{uuid.uuid4().hex[:8]}"
    task_id = f"task_{uuid.uuid4().hex[:10]}"
    brief = TaskBrief(
        workflow_id=wf, target_id=target, task_id=task_id, execution_id=execution_id,
        assigned_agent="recon-agent", objective="Controlled recovery test",
        authorized_target=target, scope_ref="scope_c2141",
        expected_activities=["parse task brief"], expected_deliverables=["result"],
        required_evidence=["structured result"],
        acceptance_criteria=AcceptanceCriteria(
            expected_agent_id="recon-agent", expected_workflow_id=wf, expected_target_id=target,
            required_actions=["parse task brief"], min_assistant_messages=1,
        ),
    )

    original_dispatch = maestro.dispatch
    original_wait = maestro.wait_for_structured_result
    original_find = maestro.find_callback_tab

    def unavailable(*args, **kwargs):
        raise RuntimeError("Usage limit reached (SIMULATED)")

    maestro.dispatch = unavailable
    blocked = run_stage(brief, settings=settings)
    maestro.dispatch = original_dispatch

    quota_messages = [
        m for m in rooms.room_messages(room.room_id, settings=settings)
        if m["message_type"] == "PROVIDER_QUOTA" and m["execution_id"] == execution_id
    ]
    after_failure_rooms = len(Database(settings.db_path).list_rooms(wf))

    result_payload = {
        "task_id": task_id, "execution_id": execution_id,
        "workflow_id": wf, "target_id": target, "authorized_target": target,
        "status": "completed", "actions_performed": ["parse task brief"],
        "evidence_refs": [], "findings": [],
    }
    lead_tab = rooms.get_room_for_agent("hackbot-lead", wf, target, settings).room_id

    def fake_dispatch(**kwargs):
        return DispatchAcknowledgment(
            task_id=task_id, execution_id=execution_id, workflow_id=wf,
            target_id=target, agent_id="recon-agent", tab_id="tab_simulated",
            lead_tab_id=lead_tab, callback_id="cb_simulated",
            status=DispatchStatus.DISPATCHED,
        )

    def fake_wait(tab_id, task_id_, execution_id_, **kwargs):
        return [
            {"role": "user", "content": task_id},
            {"role": "assistant", "content": "```json\n" + json.dumps(result_payload) + "\n```"},
        ]

    maestro.dispatch = fake_dispatch
    maestro.wait_for_structured_result = fake_wait
    maestro.find_callback_tab = lambda *a, **k: lead_tab
    maestro.resolve_agent = lambda tab_id: "recon-agent"
    resumed = resume_stage(brief, settings=settings)
    maestro.dispatch = original_dispatch
    maestro.wait_for_structured_result = original_wait
    maestro.find_callback_tab = original_find

    messages = rooms.room_messages(room.room_id, settings=settings)
    keys = [
        m["message_id"] for m in messages
        if m.get("execution_id") == execution_id and m.get("message_type") == "STRUCTURED_RESULT"
    ]
    summary = {
        "mode": "SIMULATED_PROVIDER_FAILURE",
        "blocked_decision": blocked["decision"],
        "blocked_state": blocked["state"],
        "blocked_execution": blocked["execution_id"],
        "quota_messages": len(quota_messages),
        "rooms_after_failure": after_failure_rooms,
        "resumed_decision": resumed.get("decision"),
        "resumed_state": resumed.get("state"),
        "same_execution_id": resumed.get("execution_id") == execution_id,
        "structured_result_messages": len(keys),
        "final_room_count": len(Database(settings.db_path).list_rooms(wf)),
        "sender_attribution": sorted({m["sender_agent_id"] for m in messages}),
    }
    print(json.dumps(summary, indent=2))
    ok = (
        blocked["decision"] == "BLOCKED"
        and blocked["state"] == "WAITING_FOR_USER"
        and after_failure_rooms == 1
        and len(quota_messages) == 1
        and resumed.get("decision") == "ACCEPT"
        and summary["same_execution_id"]
        and len(keys) == 1
        and summary["final_room_count"] == 1
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

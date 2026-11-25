#!/usr/bin/env python3
"""Controlled correction/clarification cycle through the real verification gate.

SIMULATED provider responses; deterministic verification, room publishing and
message deduplication are real. Real quota prevented live specialist replies.
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
from arcanum.runtime import maestro, rooms, run_stage  # noqa: E402


def main() -> int:
    settings = Settings()
    wf = f"wf_adapter_{uuid.uuid4().hex[:8]}"
    target = "SES_DED2"
    db = Database(settings.db_path)
    if not db.find_scopes_by_domain(target.lower()):
        db.save_scope(ScopeRef(scope_ref="scope_c2141", domain=target,
                               operator_id="local_operator", objective="authorized"))
    db.close()
    room = rooms.get_or_create_domain_room(wf, target, settings)
    lead_tab = rooms.get_room_for_agent("hackbot-lead", wf, target, settings).room_id

    def make_brief(execution_id: str, mode: str, parent: str) -> TaskBrief:
        return TaskBrief(
            workflow_id=wf, target_id=target,
            task_id=f"task_{uuid.uuid4().hex[:10]}", execution_id=execution_id,
            parent_task_id=parent, assigned_agent="recon-agent",
            objective=f"Controlled {mode}", authorized_target=target,
            scope_ref="scope_c2141", expected_activities=["parse task brief"],
            expected_deliverables=["result"], required_evidence=["structured result"],
            acceptance_criteria=AcceptanceCriteria(
                expected_agent_id="recon-agent", expected_workflow_id=wf,
                expected_target_id=target, required_actions=["parse task brief"],
                min_assistant_messages=1,
            ),
        )

    def result_payload(brief: TaskBrief, mode: str) -> dict:
        data = {
            "task_id": brief.task_id, "execution_id": brief.execution_id,
            "workflow_id": wf, "target_id": target, "authorized_target": target,
            "status": "completed", "actions_performed": ["parse task brief"],
            "evidence_refs": [], "findings": [],
        }
        if mode == "clarify":
            data["message_type"] = "CLARIFICATION_REQUEST"
            data["question"] = "Which target and scope should I use?"
            data["status"] = "needs_input"
        return data

    def install(mode: str):
        def fake_dispatch(**kwargs):
            return DispatchAcknowledgment(
                task_id=kwargs["task_id"], execution_id=kwargs["execution_id"],
                workflow_id=wf, target_id=target, agent_id="recon-agent",
                tab_id="tab_adapter", lead_tab_id=lead_tab,
                callback_id=f"cb_{kwargs['execution_id']}", status=DispatchStatus.DISPATCHED,
            )
        def fake_wait(tab_id, task_id_, execution_id_, **kwargs):
            brief = STAGES[execution_id_]
            return [
                {"role": "user", "content": brief.task_id},
                {"role": "assistant", "content": "```json\n" + json.dumps(result_payload(brief, mode)) + "\n```"},
            ]
        maestro.dispatch = fake_dispatch
        maestro.wait_for_structured_result = fake_wait
        maestro.resolve_agent = lambda tab_id: "recon-agent"
        maestro.find_callback_tab = lambda *a, **k: lead_tab
        maestro.send_followup = lambda *a, **k: {"success": True, "tab_id": "tab_adapter"}

    STAGES: dict[str, TaskBrief] = {}
    originals = {k: getattr(maestro, k) for k in (
        "dispatch", "wait_for_structured_result", "resolve_agent",
        "find_callback_tab", "send_followup",
    )}
    results = []
    modes = ["initial", "correction", "clarify"]
    try:
        previous = ""
        for mode in modes:
            execution_id = f"exec_{uuid.uuid4().hex[:8]}"
            brief = make_brief(execution_id, mode, previous)
            STAGES[execution_id] = brief
            install(mode)
            result = run_stage(brief, settings=settings, timeout=20)
            results.append({"mode": mode, "execution_id": execution_id,
                            "decision": result.get("decision"),
                            "state": result.get("state")})
            previous = brief.task_id
    finally:
        for k, v in originals.items():
            setattr(maestro, k, v)

    messages = rooms.room_messages(room.room_id, settings=settings)
    summary = {
        "mode": "SIMULATED_PROVIDER_RESPONSES",
        "results": results,
        "room_id": room.room_id,
        "room_count": len(Database(settings.db_path).list_rooms(wf)),
        "message_types": [m["message_type"] for m in messages],
        "senders": [m["sender_agent_id"] for m in messages],
        "clarification_response_present": any(
            m["message_type"] == "CLARIFICATION_RESPONSE" for m in messages
        ),
        "structured_results": sum(1 for m in messages if m["message_type"] == "STRUCTURED_RESULT"),
        "duplicate_message_ids": len(messages) != len({m["message_id"] for m in messages}),
    }
    print(json.dumps(summary, indent=2))
    ok = (
        [r["decision"] for r in results[:2]] == ["ACCEPT", "ACCEPT"]
        and results[2]["decision"] == "NEEDS_INPUT"
        and summary["room_count"] == 1
        and summary["clarification_response_present"]
        and not summary["duplicate_message_ids"]
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

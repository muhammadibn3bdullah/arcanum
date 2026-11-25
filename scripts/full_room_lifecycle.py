#!/usr/bin/env python3
"""C2.1.4.1: real five-agent lifecycle inside one canonical room."""

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
from arcanum.core.rooms import RoomMessageType  # noqa: E402
from arcanum.core.task_brief import TaskBrief  # noqa: E402
from arcanum.memory.database import Database  # noqa: E402
from arcanum.runtime import rooms, run_stage  # noqa: E402

ORDER = ["recon-agent", "hunt-agent", "validate-agent", "report-agent"]


def brief(wf: str, target: str, agent: str, previous: list[dict], objective: str) -> TaskBrief:
    return TaskBrief(
        workflow_id=wf, target_id=target,
        task_id=f"task_{uuid.uuid4().hex[:10]}",
        execution_id=f"exec_{uuid.uuid4().hex[:8]}",
        parent_task_id=previous[-1]["task_id"] if previous else "",
        assigned_agent=agent, objective=objective,
        authorized_target=target, scope_ref="scope_c2141",
        operation_constraints=["authorized scope only", "sequential bounded execution"],
        available_capabilities=["caido_read", "http_security_test"],
        relevant_findings=[f.get("task_id", "") for f in previous],
        evidence_refs=[],
        expected_activities=["parse task brief", "return structured result"],
        expected_deliverables=["structured result"],
        acceptance_criteria=AcceptanceCriteria(
            expected_agent_id=agent, expected_workflow_id=wf, expected_target_id=target,
            required_actions=["parse task brief"],
            required_structured_fields=["workflow_id", "target_id", "authorized_target"],
            required_field_values={"workflow_id": wf, "target_id": target, "authorized_target": target},
            min_assistant_messages=1,
        ),
        required_evidence=["task-specific structured fields"],
    )


def main() -> int:
    settings = Settings()
    wf = f"wf_room_cycle_{uuid.uuid4().hex[:8]}"
    target = "SES_DED2"
    db = Database(settings.db_path)
    if not db.find_scopes_by_domain(target.lower()):
        db.save_scope(ScopeRef(
            scope_ref="scope_c2141", domain=target,
            operator_id="local_operator", objective="C2.1.4.1 authorized target",
        ))
    db.close()
    room = rooms.get_or_create_domain_room(wf, target, settings)

    objectives = {
        "recon-agent": "Identify target and scope for SES_DED2. Reasoning only, no tools. Return findings: [].",
        "hunt-agent": "Using accepted recon context, rank HIGH/CRITICAL hypotheses for SES_DED2. Reasoning only. Return findings: [].",
        "validate-agent": "Apply proof gate to accepted context for SES_DED2. Reasoning only. Return findings: [].",
        "report-agent": "Draft final report for SES_DED2 from accepted results. Reasoning only. Return findings: [].",
    }
    stages = []
    for agent in ORDER:
        task = brief(wf, target, agent, stages, objectives[agent])
        result = run_stage(task, settings=settings, timeout=240)
        stages.append(result)
        if result.get("decision") != "ACCEPT":
            print(json.dumps({
                "decision": "REJECT", "failed_agent": agent,
                "reasons": result.get("reasons", []), "stages": stages,
            }, indent=2))
            return 1

    all_messages = rooms.room_messages(room.room_id, settings=settings)
    per_execution = {}
    for stage in stages:
        eid = stage["execution_id"]
        per_execution[eid] = [
            {"message_id": m["message_id"], "sender": m["sender_agent_id"], "type": m["message_type"]}
            for m in all_messages if m.get("execution_id") == eid
        ]

    final = rooms.publish_message(
        wf, target, "hackbot-lead", RoomMessageType.FINAL_REPORT,
        json.dumps({"decision": "ACCEPT", "workflow_id": wf, "target_id": target}),
        idempotency_key=f"final:{wf}", settings=settings,
    )
    views = {
        a: rooms.get_room_for_agent(a, wf, target, settings).room_id
        for a in ["hackbot-lead", *ORDER]
    }
    summary = {
        "decision": "ACCEPT",
        "workflow_id": wf,
        "target_id": target,
        "room_id": room.room_id,
        "stages": {
            s["task_id"]: {
                "agent": next(a for a in ORDER if a in {s["report"]["agent_id"]}),
                "decision": s["decision"], "state": s["state"],
                "execution_id": s["execution_id"], "tab_id": s["tab_id"],
            } for s in stages
        },
        "per_execution_room_messages": per_execution,
        "final_report_message": final.message_id,
        "agent_views": views,
        "all_views_same_room": len(set(views.values())) == 1,
        "canonical_rooms_for_workflow": len(Database(settings.db_path).list_rooms(wf)),
    }
    print(json.dumps(summary, indent=2))
    return 0 if summary["all_views_same_room"] and summary["canonical_rooms_for_workflow"] == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())

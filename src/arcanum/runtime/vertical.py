"""Single-agent vertical integration path.

Head Agent task brief → Maestro dispatch → OpenCode specialist execution →
Maestro transcript collection → deterministic verification → consistent state.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from ..config.settings import Settings
from ..core.execution import (
    AcceptanceCriteria,
    ExecutionReport,
    ExecutionState,
    VerificationDecision,
    VerificationResult,
)
from ..core.task_brief import TaskBrief
from ..core.verification import CompletionVerifier, parse_structured_result
from ..memory.database import Database
from . import maestro
from .tab_registry import TabRegistry


def _assistant_text(transcript: list[dict[str, Any]]) -> str:
    return "\n".join(
        str(m.get("content", ""))
        for m in transcript
        if m.get("role") == "assistant"
    )


def _strings(value: Any) -> list[str]:
    """Normalize structured list fields to strings without losing evidence."""
    if value is None:
        return []
    if not isinstance(value, list):
        return [str(value)]
    out = []
    for item in value:
        if isinstance(item, str):
            out.append(item)
        else:
            out.append(json.dumps(item, ensure_ascii=False))
    return out


def _report_from_structured(
    structured: dict[str, Any],
    brief: TaskBrief,
    execution_id: str,
    trusted_agent: str,
) -> ExecutionReport | None:
    status_value = str(structured.get("status", "")).lower()
    try:
        status = ExecutionState(status_value)
    except ValueError:
        return None
    if str(structured.get("task_id", "")) != brief.task_id:
        return None
    if str(structured.get("execution_id", "")) != execution_id:
        return None
    return ExecutionReport(
        workflow_id=brief.workflow_id,
        target_id=brief.target_id,
        task_id=brief.task_id,
        execution_id=execution_id,
        agent_id=trusted_agent,
        status=status,
        lifecycle_state="RESULT_RECEIVED",
        objective=brief.objective,
        completed_at=datetime.now(timezone.utc),
        actions_performed=_strings(structured.get("actions_performed")),
        tool_execution_refs=_strings(structured.get("tool_execution_refs")),
        authorization_refs=_strings(structured.get("authorization_refs")),
        findings=_strings(structured.get("findings")),
        evidence_refs=_strings(structured.get("evidence_refs")),
        coverage=dict(structured.get("coverage", {}) or {}),
        limitations=_strings(structured.get("limitations")),
        errors=_strings(structured.get("errors")),
        recommended_next_steps=_strings(structured.get("recommended_next_steps")),
    )


def _lifecycle_state(decision: str) -> str:
    return {
        "ACCEPT": "ACCEPTED",
        "NEEDS_VALIDATION": "VERIFYING",
        "PARTIALLY_ACCEPTED": "PARTIALLY_ACCEPTED",
        "REJECT": "REJECTED",
        "BLOCKED": "BLOCKED",
        "FAILED": "FAILED",
    }.get(decision, "FAILED")


def run_one_specialist(
    agent: str,
    objective: str,
    workflow_id: str = "",
    target_id: str = "",
    authorized_target: str | None = None,
    scope_ref: str = "",
    settings: Settings | None = None,
    timeout: int = 180,
    registry: TabRegistry | None = None,
    lead_tab_id: str = "",
) -> dict[str, Any]:
    settings = settings or Settings()
    registry = registry or TabRegistry(settings.tab_registry_path)
    task_id = f"task_vert_{uuid.uuid4().hex[:8]}"
    execution_id = f"exec_vert_{uuid.uuid4().hex[:8]}"
    workflow_id = workflow_id or f"workflow_vert_{uuid.uuid4().hex[:8]}"
    target_id = target_id or "lab.local"

    brief = TaskBrief(
        workflow_id=workflow_id,
        target_id=target_id,
        task_id=task_id,
        execution_id=execution_id,
        assigned_agent=agent,
        objective=objective,
        authorized_target=authorized_target or target_id,
        scope_ref=scope_ref or f"scope_{target_id}",
        operation_constraints=["passive", "authorized scope only"],
        available_capabilities=["caido_read", "http_read"],
        expected_activities=["analyze assigned target", "return structured result"],
        expected_deliverables=["structured execution result", "evidence references"],
        acceptance_criteria=AcceptanceCriteria(
            expected_agent_id=agent,
            required_actions=["analyze assigned target"],
            required_evidence_refs=[f"maestro:tab:{{tab}}"],
            min_assistant_messages=1,
        ),
        required_evidence=["structured result with actions, evidence, and status"],
    )
    errors = brief.completeness_errors()
    if errors:
        return {"decision": "REJECT", "reasons": [f"Incomplete task brief: {errors}"]}

    brief.acceptance_criteria.expected_workflow_id = workflow_id
    brief.acceptance_criteria.expected_target_id = target_id

    ack = maestro.dispatch(
        agent=agent,
        prompt=brief.to_prompt(),
        task_id=task_id,
        execution_id=execution_id,
        workflow_id=workflow_id,
        target_id=target_id,
        registry=registry,
        lead_tab_id=lead_tab_id,
    )
    lead_tab = ack.lead_tab_id or lead_tab_id
    transcript = maestro.wait_for_structured_result(
        ack.tab_id, task_id, execution_id,
        cursor=ack.transcript_cursor,
        workflow_id=workflow_id,
        target_id=target_id,
        timeout=timeout,
    )
    trusted_agent = maestro.resolve_agent(ack.tab_id)
    structured = parse_structured_result(_assistant_text(transcript)) or {}
    report = _report_from_structured(structured, brief, execution_id, trusted_agent)

    criteria = brief.acceptance_criteria
    criteria.required_evidence_refs = [
        ref.format(tab=ack.tab_id) for ref in criteria.required_evidence_refs
    ]
    if report is None:
        verification = VerificationResult(
            decision=VerificationDecision.REJECT,
            reasons=["No valid structured execution report could be derived"],
            checks={"structured_result": False},
        )
        report = ExecutionReport(
            workflow_id=workflow_id, target_id=target_id, task_id=task_id,
            execution_id=execution_id, agent_id=trusted_agent,
            status=ExecutionState.FAILED, lifecycle_state="FAILED",
        )
    else:
        verification = CompletionVerifier().verify(
            report=report, ack=ack, criteria=criteria,
            transcript=transcript, trusted_agent_id=trusted_agent,
        )

    callback_tab = maestro.find_callback_tab(
        task_id, execution_id,
        lead_tab_id=lead_tab or None,
        target_id=target_id,
        workflow_id=workflow_id,
        registry=registry,
    )
    verification.checks["callback_correlated"] = bool(callback_tab)
    verification.checks["callback_on_lead_tab"] = bool(callback_tab) and (
        not lead_tab or callback_tab == lead_tab
    )
    if not callback_tab:
        verification.decision = VerificationDecision.REJECT
        verification.reasons.append(
            "No correlated Head Agent callback tab found on the central Lead tab"
        )

    state = _lifecycle_state(verification.decision.value)
    report.lifecycle_state = state
    db = Database(settings.db_path)
    db.save_callback(
        callback_id=ack.callback_id,
        workflow_id=workflow_id,
        target_id=target_id,
        task_id=task_id,
        execution_id=execution_id,
        agent_id=trusted_agent,
        specialist_tab_id=ack.specialist_tab_id,
        lead_tab_id=lead_tab,
        status="correlated" if callback_tab else "created",
        data={
            "callback_tab": callback_tab,
            "callback_prompt": ack.callback_prompt,
            "raw": ack.raw,
        },
    )
    db.save_execution(
        execution_id=execution_id,
        workflow_id=workflow_id,
        target_id=target_id,
        task_id=task_id,
        agent_id=trusted_agent,
        tab_id=ack.tab_id,
        data={
            "ack": ack.model_dump(mode="json"),
            "report": report.model_dump(mode="json"),
            "verification": verification.model_dump(mode="json"),
            "lifecycle_state": state,
            "callback_tab": callback_tab,
            "callback_id": ack.callback_id,
            "lead_tab_id": lead_tab,
            "specialist_tab_id": ack.specialist_tab_id,
            "tab_reused": ack.tab_reused,
            "transcript_cursor": ack.transcript_cursor,
        },
    )
    db.close()
    return {
        "task_id": task_id,
        "execution_id": execution_id,
        "workflow_id": workflow_id,
        "target_id": target_id,
        "trusted_agent": trusted_agent,
        "tab_id": ack.tab_id,
        "specialist_tab_id": ack.specialist_tab_id,
        "lead_tab_id": lead_tab,
        "tab_reused": ack.tab_reused,
        "transcript_cursor": ack.transcript_cursor,
        "callback_id": ack.callback_id,
        "callback_tab": callback_tab,
        "decision": verification.decision.value,
        "lifecycle_state": state,
        "reasons": verification.reasons,
        "report": report.model_dump(mode="json"),
        "verification": verification.model_dump(mode="json"),
    }

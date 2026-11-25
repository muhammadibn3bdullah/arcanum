"""Head Agent supervisor for deterministic multi-agent workflow stages.

C2.1.1: every dispatch and follow-up goes through the persistent, target-scoped
Maestro tab registry. The workflow has one central Lead tab; each specialist
has one tab per (workflow_id, target_id, agent_id).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from ..config.settings import Settings
from ..core.execution import AcceptanceCriteria, VerificationDecision
from ..core.lifecycle import LifecycleState, MessageType
from ..core.task_brief import TaskBrief
from ..memory.database import Database
from . import maestro, rooms
from .communication import detect_message, respond_to_specialist
from ..core.rooms import RoomMessageType
from .vertical import _report_from_structured, _lifecycle_state
from .tab_registry import TabRegistry
from ..core.verification import CompletionVerifier, parse_structured_result


STAGE_AGENTS = ["recon-agent", "hunt-agent", "validate-agent", "report-agent"]


def _is_provider_failure(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(k in text for k in (
        "usage limit", "quota", "rate limit", "provider unavailable",
        "authentication failed", "capacity",
    ))


def _publish(
    workflow_id: str, target_id: str, sender: str, kind: RoomMessageType,
    content: str, *, recipient: str = "", task_id: str = "",
    execution_id: str = "", correlation_id: str = "",
    internal_session_id: str = "", idempotency_key: str = "",
    evidence_refs: list[str] | None = None, settings: Settings,
) -> None:
    """Best-effort publish to the canonical room; never block verification."""
    try:
        rooms.publish_message(
            workflow_id, target_id, sender, kind, content,
            recipient_agent_id=recipient, task_id=task_id,
            execution_id=execution_id, correlation_id=correlation_id,
            internal_session_id=internal_session_id, idempotency_key=idempotency_key,
            evidence_refs=evidence_refs, settings=settings,
        )
    except (PermissionError, ValueError, RuntimeError):
        pass


def resume_stage(
    brief: TaskBrief,
    settings: Settings | None = None,
    timeout: int = 300,
    registry: TabRegistry | None = None,
    lead_tab_id: str = "",
) -> dict[str, Any]:
    """Resume an unfinished execution with the same logical task/execution IDs.

    Provider availability is expected to have been restored by the caller. The
    existing deterministic verification gate still applies to the new result.
    """
    settings = settings or Settings()
    db = Database(settings.db_path)
    previous = db.get_execution(brief.execution_id)
    db.close()
    if previous and previous.get("state") not in (
        LifecycleState.WAITING_FOR_USER.value,
        LifecycleState.WAITING_FOR_AUTHORIZATION.value,
        LifecycleState.BLOCKED.value,
    ):
        return {
            "decision": "REJECT",
            "reasons": ["Execution is not in a resumable state"],
            "state": previous.get("state"),
        }
    return run_stage(
        brief, settings=settings, timeout=timeout,
        registry=registry, lead_tab_id=lead_tab_id,
        notify_head=False, resumed=True,
    )


def build_stage_brief(
    workflow_id: str,
    target_id: str,
    assigned_agent: str,
    objective: str,
    execution_id: str,
    previous_results: list[dict[str, Any]],
) -> TaskBrief:
    parent = previous_results[-1]["task_id"] if previous_results else ""
    findings = []
    evidence = []
    for prev in previous_results:
        findings.extend(prev.get("report", {}).get("findings", []))
        evidence.extend(prev.get("report", {}).get("evidence_refs", []))

    if assigned_agent == "recon-agent":
        activities = ["inspect authorized scope", "identify attack-surface candidates"]
        deliverables = ["structured execution result", "evidence references"]
        capabilities = ["caido_read", "http_read"]
    elif assigned_agent == "hunt-agent":
        activities = ["rank HIGH/CRITICAL hunt candidates", "analyze accepted recon context"]
        deliverables = ["structured execution result", "ranked hypotheses"]
        capabilities = ["http_security_test", "caido_read"]
    elif assigned_agent == "validate-agent":
        activities = ["apply proof gate", "validate accepted hypotheses"]
        deliverables = ["structured execution result", "validation status"]
        capabilities = ["http_validation", "finding_status_write"]
    else:
        activities = ["draft report from accepted findings", "preserve evidence references"]
        deliverables = ["structured execution result", "report draft"]
        capabilities = ["report_write"]

    return TaskBrief(
        workflow_id=workflow_id,
        target_id=target_id,
        task_id=f"task_{uuid.uuid4().hex[:10]}",
        execution_id=execution_id,
        parent_task_id=parent,
        assigned_agent=assigned_agent,
        objective=objective,
        authorized_target=target_id,
        scope_ref=f"scope_{target_id}",
        operation_constraints=["authorized scope only", "no out-of-scope expansion"],
        available_capabilities=capabilities,
        relevant_findings=findings,
        evidence_refs=evidence,
        expected_activities=activities,
        expected_deliverables=deliverables,
        acceptance_criteria=AcceptanceCriteria(
            expected_agent_id=assigned_agent,
            expected_workflow_id=workflow_id,
            expected_target_id=target_id,
            required_actions=activities,
            required_structured_fields=["workflow_id", "target_id"],
            required_field_values={"workflow_id": workflow_id, "target_id": target_id},
            min_assistant_messages=1,
        ),
        required_evidence=["structured result with actions, evidence, and status"],
    )


def run_stage(
    brief: TaskBrief,
    settings: Settings | None = None,
    timeout: int = 300,
    registry: TabRegistry | None = None,
    lead_tab_id: str = "",
    notify_head: bool = True,
    resumed: bool = False,
) -> dict[str, Any]:
    settings = settings or Settings()
    db = Database(settings.db_path)
    registry = registry or TabRegistry(settings.tab_registry_path)
    prior = db.get_execution(brief.execution_id) or {}

    # Isolation criteria are always derived from the trusted brief, never from
    # an agent-supplied value.
    brief.acceptance_criteria.expected_workflow_id = brief.workflow_id
    brief.acceptance_criteria.expected_target_id = brief.target_id

    errors = brief.completeness_errors()
    if errors:
        db.close()
        return {"decision": "REJECT", "reasons": [f"Incomplete task brief: {errors}"]}

    try:
        ack = maestro.dispatch(
            agent=brief.assigned_agent,
            prompt=brief.to_prompt(),
            task_id=brief.task_id,
            execution_id=brief.execution_id,
            workflow_id=brief.workflow_id,
            target_id=brief.target_id,
            registry=registry,
            lead_tab_id=lead_tab_id,
            notify_head=notify_head and not resumed,
        )
    except Exception as exc:
        if _is_provider_failure(exc):
            _publish(
                brief.workflow_id, brief.target_id, brief.assigned_agent,
                RoomMessageType.PROVIDER_QUOTA, str(exc),
                recipient="hackbot-lead", task_id=brief.task_id,
                execution_id=brief.execution_id,
                idempotency_key=f"quota:{brief.execution_id}",
                settings=settings,
            )
            db.transition_state(
                brief.workflow_id, brief.target_id, brief.task_id, brief.execution_id,
                LifecycleState.PENDING.value, LifecycleState.WAITING_FOR_USER.value,
                f"Provider unavailable for {brief.assigned_agent}: {exc}",
            )
            db.close()
            return {
                "decision": "BLOCKED", "state": LifecycleState.WAITING_FOR_USER.value,
                "reasons": [f"Provider unavailable for {brief.assigned_agent}: {exc}"],
                "agent_id": brief.assigned_agent,
                "task_id": brief.task_id,
                "execution_id": brief.execution_id,
            }
        raise
    lead_tab = ack.lead_tab_id or lead_tab_id
    db.transition_state(
        brief.workflow_id, brief.target_id, brief.task_id, brief.execution_id,
        LifecycleState.PENDING.value, LifecycleState.DISPATCHED.value,
        "Maestro dispatch acknowledged",
    )
    db.save_message(
        brief.workflow_id, brief.target_id, brief.task_id, brief.execution_id,
        "hackbot-lead", brief.assigned_agent, MessageType.TASK_DISPATCH.value,
        brief.to_prompt(),
        {
            "tab_id": ack.tab_id,
            "specialist_tab_id": ack.specialist_tab_id,
            "lead_tab_id": lead_tab,
            "tab_reused": ack.tab_reused,
            "transcript_cursor": ack.transcript_cursor,
        },
    )
    if not resumed:
        db.save_callback(
            callback_id=ack.callback_id,
            workflow_id=brief.workflow_id,
            target_id=brief.target_id,
            task_id=brief.task_id,
            execution_id=brief.execution_id,
            agent_id=brief.assigned_agent,
            specialist_tab_id=ack.specialist_tab_id,
            lead_tab_id=lead_tab,
            status="created",
            data={"callback_prompt": ack.callback_prompt, "raw": ack.raw},
        )
    else:
        # The original callback remains armed; resumption must not register a
        # second one. Reuse the exact callback correlation from the first try.
        prior_cb = prior.get("ack", {}) or {}
        callback_id = str(prior_cb.get("callback_id") or ack.callback_id)
        lead_tab = str(prior.get("lead_tab_id") or lead_tab)
    _publish(
        brief.workflow_id, brief.target_id, "hackbot-lead",
        RoomMessageType.TASK_ASSIGNMENT, brief.to_prompt(),
        recipient=brief.assigned_agent, task_id=brief.task_id,
        execution_id=brief.execution_id, correlation_id=ack.callback_id,
        internal_session_id=ack.tab_id,
        idempotency_key=f"task:{brief.task_id}:{brief.execution_id}",
        settings=settings,
    )

    try:
        transcript = maestro.wait_for_structured_result(
            ack.tab_id, brief.task_id, brief.execution_id,
            cursor=ack.transcript_cursor,
            workflow_id=brief.workflow_id,
            target_id=brief.target_id,
            timeout=timeout,
        )
    except TimeoutError as exc:
        live = maestro.get_transcript(ack.tab_id)
        quota = [
            str(m.get("content", ""))
            for m in live
            if m.get("role") == "system"
            and _is_provider_failure(Exception(str(m.get("content", ""))))
        ]
        if quota:
            _publish(
                brief.workflow_id, brief.target_id, brief.assigned_agent,
                RoomMessageType.PROVIDER_QUOTA, quota[-1],
                recipient="hackbot-lead", task_id=brief.task_id,
                execution_id=brief.execution_id, correlation_id=ack.callback_id,
                internal_session_id=ack.tab_id,
                idempotency_key=f"quota:{brief.execution_id}",
                settings=settings,
            )
            db.transition_state(
                brief.workflow_id, brief.target_id, brief.task_id, brief.execution_id,
                LifecycleState.DISPATCHED.value, LifecycleState.WAITING_FOR_USER.value,
                f"Provider unavailable for {brief.assigned_agent}: {quota[-1]}",
            )
            db.save_execution(
                brief.execution_id, brief.workflow_id, brief.target_id,
                brief.task_id, brief.assigned_agent, ack.tab_id,
                {
                    "ack": ack.model_dump(mode="json"),
                    "brief": brief.model_dump(mode="json"),
                    "state": LifecycleState.WAITING_FOR_USER.value,
                    "provider_error": quota[-1],
                    "resume_cursor": len(live),
                },
            )
            db.close()
            return {
                "decision": "BLOCKED",
                "state": LifecycleState.WAITING_FOR_USER.value,
                "reasons": [f"Provider unavailable for {brief.assigned_agent}: {quota[-1]}"],
                "agent_id": brief.assigned_agent,
                "task_id": brief.task_id,
                "execution_id": brief.execution_id,
                "tab_id": ack.tab_id,
                "provider_error": quota[-1],
                "resume_cursor": len(live),
            }
        raise exc
    trusted_agent = maestro.resolve_agent(ack.tab_id)
    msg_type, data = detect_message(transcript, brief.task_id, brief.execution_id)
    if msg_type == MessageType.CLARIFICATION_REQUEST:
        answer = respond_to_specialist(
            brief, str(data.get("question", "")), ack.tab_id, db,
            registry=registry, task_id=brief.task_id,
            execution_id=brief.execution_id,
        )
        db.close()
        return {
            "decision": "NEEDS_INPUT",
            "message_type": msg_type.value,
            "question": data.get("question"),
            "clarification": answer,
            "state": answer["state"],
            "tab_id": ack.tab_id,
            "specialist_tab_id": ack.specialist_tab_id,
            "lead_tab_id": lead_tab,
        }

    report = _report_from_structured(data, brief, brief.execution_id, trusted_agent)
    _publish(
        brief.workflow_id, brief.target_id, brief.assigned_agent,
        RoomMessageType.STRUCTURED_RESULT, json.dumps(data, ensure_ascii=False),
        recipient="hackbot-lead", task_id=brief.task_id,
        execution_id=brief.execution_id, correlation_id=ack.callback_id,
        internal_session_id=ack.tab_id,
        evidence_refs=data.get("evidence_refs", []) if isinstance(data, dict) else [],
        idempotency_key=f"result:{brief.execution_id}",
        settings=settings,
    )

    if report is None:
        db.transition_state(
            brief.workflow_id, brief.target_id, brief.task_id, brief.execution_id,
            LifecycleState.DISPATCHED.value, LifecycleState.FAILED.value,
            "No valid structured execution report",
        )
        db.close()
        return {"decision": "FAILED", "reasons": ["No valid structured execution report"]}

    verification = CompletionVerifier().verify(
        report=report, ack=ack, criteria=brief.acceptance_criteria,
        transcript=transcript, trusted_agent_id=trusted_agent,
    )
    if resumed and db.get_callback(callback_id):
        # Original callback still correlated to this logical execution.
        callback_tab = lead_tab
    else:
        callback_tab = maestro.find_callback_tab(
            brief.task_id, brief.execution_id,
            lead_tab_id=lead_tab or None,
            target_id=brief.target_id,
            workflow_id=brief.workflow_id,
            registry=registry,
        )
    verification.checks["callback_correlated"] = bool(callback_tab)
    verification.checks["callback_on_lead_tab"] = bool(callback_tab) and (
        not lead_tab or callback_tab == lead_tab
    )
    verification.checks["target_isolated"] = verification.checks.get(
        "structured_target_isolated", True
    )
    if not callback_tab:
        verification.decision = VerificationDecision.REJECT
        verification.reasons.append(
            "No correlated Head Agent callback tab found on the central Lead tab"
        )
    elif lead_tab and callback_tab != lead_tab:
        verification.decision = VerificationDecision.REJECT
        verification.reasons.append(
            "Callback arrived on a non-central Lead tab"
        )
    if not verification.checks["target_isolated"]:
        verification.decision = VerificationDecision.REJECT
        verification.reasons.append("Result rejected by cross-target isolation gate")

    if callback_tab:
        db.save_callback(
            callback_id=ack.callback_id,
            workflow_id=brief.workflow_id,
            target_id=brief.target_id,
            task_id=brief.task_id,
            execution_id=brief.execution_id,
            agent_id=brief.assigned_agent,
            specialist_tab_id=ack.specialist_tab_id,
            lead_tab_id=lead_tab,
            status="correlated",
            data={
                "callback_tab": callback_tab,
                "callback_prompt": ack.callback_prompt,
                "raw": ack.raw,
            },
        )

    state = _lifecycle_state(verification.decision.value)
    report.lifecycle_state = state
    db.transition_state(
        brief.workflow_id, brief.target_id, brief.task_id, brief.execution_id,
        LifecycleState.DISPATCHED.value, state,
        "; ".join(verification.reasons) or "verification completed",
    )
    db.save_message(
        brief.workflow_id, brief.target_id, brief.task_id, brief.execution_id,
        "hackbot-lead", brief.assigned_agent, MessageType.VERIFICATION_RESULT.value,
        verification.decision.value, verification.model_dump(mode="json"),
    )
    _publish(
        brief.workflow_id, brief.target_id, "hackbot-lead",
        RoomMessageType.VERIFICATION_DECISION, verification.decision.value,
        recipient=brief.assigned_agent, task_id=brief.task_id,
        execution_id=brief.execution_id, correlation_id=ack.callback_id,
        idempotency_key=f"verification:{brief.execution_id}",
        settings=settings,
    )
    db.save_execution(
        brief.execution_id, brief.workflow_id, brief.target_id, brief.task_id,
        trusted_agent, ack.tab_id,
        {
            "ack": ack.model_dump(mode="json"),
            "brief": brief.model_dump(mode="json"),
            "report": report.model_dump(mode="json"),
            "verification": verification.model_dump(mode="json"),
            "state": state,
            "callback_tab": callback_tab,
            "callback_id": ack.callback_id,
            "lead_tab_id": lead_tab,
            "specialist_tab_id": ack.specialist_tab_id,
            "tab_reused": ack.tab_reused,
            "transcript_cursor": ack.transcript_cursor,
            "transcript": transcript,
        },
    )
    db.close()
    return {
        "decision": verification.decision.value,
        "state": state,
        "reasons": verification.reasons,
        "report": report.model_dump(mode="json"),
        "verification": verification.model_dump(mode="json"),
        "tab_id": ack.tab_id,
        "specialist_tab_id": ack.specialist_tab_id,
        "lead_tab_id": lead_tab,
        "tab_reused": ack.tab_reused,
        "transcript_cursor": ack.transcript_cursor,
        "callback_id": ack.callback_id,
        "callback_tab": callback_tab,
        "task_id": brief.task_id,
        "execution_id": brief.execution_id,
    }

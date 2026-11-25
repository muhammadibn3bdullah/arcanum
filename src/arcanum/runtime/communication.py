"""Head Agent communication and clarification routing.

The Head Agent is implemented as deterministic application logic working from
trusted Maestro records and the original task brief. It does not invent data.
"""

from __future__ import annotations

import re
from typing import Any

from ..core.lifecycle import LifecycleState, MessageType, WorkflowMessage
from ..core.rooms import RoomMessageType
from ..core.task_brief import TaskBrief
from ..core.verification import parse_structured_result
from . import maestro, rooms

QUESTION_HINTS = re.compile(
    r"\b(which|what|where|how|scope|target|domain|objective|grant|permission|output)\b.*\?",
    re.I,
)


def detect_message(
    transcript: list[dict[str, Any]], task_id: str, execution_id: str
) -> tuple[MessageType, dict[str, Any]]:
    """Detect clarification requests or execution results in trusted transcript."""
    for message in reversed(transcript):
        if message.get("role") != "assistant":
            continue
        text = str(message.get("content", ""))
        data = parse_structured_result(text)
        if data and (
            str(data.get("task_id", "")) == task_id
            and str(data.get("execution_id", "")) == execution_id
        ):
            msg_type = str(data.get("message_type", "")).upper()
            if msg_type in {"CLARIFICATION_REQUEST", "EXECUTION_RESULT"}:
                return MessageType(msg_type), data
            if data.get("status") or data.get("actions_performed"):
                return MessageType.EXECUTION_RESULT, data
        if re.search(r"```json\s*\{", text, re.I):
            parsed = parse_structured_result(text)
            if parsed:
                return MessageType.EXECUTION_RESULT, parsed
        if QUESTION_HINTS.search(text):
            return MessageType.CLARIFICATION_REQUEST, {"question": text}
    return MessageType.PROGRESS_UPDATE, {}


def resolve_clarification(brief: TaskBrief, question: str) -> tuple[str, LifecycleState]:
    """Resolve a clarification using the brief and trusted context only."""
    q = question.lower()
    parts = []
    if re.search(r"domain|target|host|scope", q):
        parts.append(
            f"Authorized target: {brief.authorized_target} "
            f"(target_id={brief.target_id}, scope_ref={brief.scope_ref})"
        )
    if re.search(r"objective|task|what.*do", q):
        parts.append(f"Objective: {brief.objective}")
    if re.search(r"preferred|customer|client preference", q):
        return (
            "The question requires a user decision not present in the task brief or trusted context.",
            LifecycleState.WAITING_FOR_USER,
        )
    if re.search(r"output|deliverable|format", q):
        parts.append(
            "Deliverables: " + "; ".join(brief.expected_deliverables)
            + f". Expected output: {brief.expected_output}"
        )
    if re.search(r"permission|grant|capability|tool", q):
        parts.append(
            "Available capabilities: " + ", ".join(brief.available_capabilities)
            + ". Capabilities require a DPM-issued grant_id; the Head Agent cannot grant permissions."
        )
        return " ".join(parts) or "Use the task brief as authoritative.", LifecycleState.WAITING_FOR_AUTHORIZATION

    if parts:
        return (
            "Head Agent clarification from the existing task brief: "
            + " ".join(parts)
            + " Continue the original task. Do not ask the user for information already supplied.",
            LifecycleState.RUNNING,
        )

    return (
        "The question requires a user decision not present in the task brief or trusted context.",
        LifecycleState.WAITING_FOR_USER,
    )


def respond_to_specialist(
    brief: TaskBrief,
    question: str,
    tab_id: str,
    db=None,
    registry=None,
    task_id: str = "",
    execution_id: str = "",
) -> dict[str, Any]:
    """Send a correlated clarification response to the same target-specific tab."""
    response, state = resolve_clarification(brief, question)
    sent = maestro.send_followup(
        tab_id,
        response,
        agent=brief.assigned_agent,
        registry=registry,
        workflow_id=brief.workflow_id,
        target_id=brief.target_id,
        task_id=task_id or brief.task_id,
        execution_id=execution_id or brief.execution_id,
    )
    try:
        rooms.publish_message(
            brief.workflow_id, brief.target_id, "hackbot-lead",
            RoomMessageType.CLARIFICATION_RESPONSE, response,
            recipient_agent_id=brief.assigned_agent,
            task_id=task_id or brief.task_id,
            execution_id=execution_id or brief.execution_id,
            correlation_id=str(sent.get("raw", {}).get("callbackId") or ""),
            internal_session_id=tab_id,
            idempotency_key=f"clarification:{brief.execution_id}:{response[:80]}",
        )
    except (PermissionError, ValueError, RuntimeError):
        pass
    if db is not None:
        db.save_message(
            brief.workflow_id, brief.target_id, brief.task_id, brief.execution_id,
            "hackbot-lead", brief.assigned_agent, MessageType.CLARIFICATION_RESPONSE.value,
            response,
            {
                "tab_id": tab_id,
                "specialist_tab_id": tab_id,
                "state": state.value,
                "sent": sent,
            },
        )
        db.transition_state(
            brief.workflow_id, brief.target_id, brief.task_id, brief.execution_id,
            LifecycleState.NEEDS_INPUT.value, state.value,
            "Head Agent clarification response sent to correlated specialist session",
        )
    return {
        "response": response,
        "state": state.value,
        "sent": sent,
        "tab_id": tab_id,
    }

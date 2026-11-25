"""Execution lifecycle data contracts.

These separate a dispatch acknowledgment from a real execution result and a
Head Agent decision. Acknowledgment is never completion.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DispatchStatus(str, Enum):
    DISPATCHED = "dispatched"
    ACKNOWLEDGED = "acknowledged"
    CALLBACK_RECEIVED = "callback_received"
    RESULT_COLLECTED = "result_collected"


class ExecutionState(str, Enum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class VerificationDecision(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    PARTIALLY_ACCEPTED = "PARTIALLY_ACCEPTED"
    NEEDS_VALIDATION = "NEEDS_VALIDATION"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class DispatchAcknowledgment(BaseModel):
    """Proof that Maestro accepted a dispatch — not proof of execution."""

    task_id: str
    execution_id: str
    workflow_id: str = ""
    target_id: str = ""
    agent_id: str
    tab_id: str
    agent_session_id: str = ""
    callback_id: str = ""
    # C2.1.1 persistent-tab routing evidence
    specialist_tab_id: str = ""
    lead_tab_id: str = ""
    transcript_cursor: int = 0
    tab_reused: bool = False
    callback_prompt: str = ""
    status: DispatchStatus = DispatchStatus.DISPATCHED
    created_at: datetime = Field(default_factory=_now)
    raw: dict[str, Any] = Field(default_factory=dict)
    model_config = {"extra": "forbid"}


class ExecutionReport(BaseModel):
    """Structured result returned after an actual specialist execution."""

    workflow_id: str
    target_id: str
    task_id: str
    execution_id: str
    agent_id: str
    status: ExecutionState
    lifecycle_state: str = "RESULT_RECEIVED"
    objective: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    actions_performed: list[str] = Field(default_factory=list)
    tool_execution_refs: list[str] = Field(default_factory=list)
    authorization_refs: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)
    model_config = {"extra": "forbid"}


class AcceptanceCriteria(BaseModel):
    """Task-specific criteria. Verification must not invent requirements."""

    expected_agent_id: str = ""
    expected_workflow_id: str = ""
    expected_target_id: str = ""
    expected_marker: str = ""
    required_actions: list[str] = Field(default_factory=list)
    required_evidence_refs: list[str] = Field(default_factory=list)
    required_structured_fields: list[str] = Field(default_factory=list)
    required_field_values: dict[str, str] = Field(default_factory=dict)
    require_tool_execution: bool = False
    min_assistant_messages: int = 1
    reject_acknowledgment_only: bool = True
    model_config = {"extra": "forbid"}


class VerificationResult(BaseModel):
    decision: VerificationDecision
    reasons: list[str] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)
    verified_at: datetime = Field(default_factory=_now)
    model_config = {"extra": "forbid"}

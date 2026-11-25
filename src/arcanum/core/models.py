"""Pydantic models for Phase 1.

These are the only data structures agents exchange. Agent messages never carry
authorization; they carry task IDs, objective text, finding references, and scope
references. All such data is treated as untrusted input by receivers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TaskStatus(str, Enum):
    CREATED = "created"
    AUTHORIZED = "authorized"
    DISPATCHED = "dispatched"
    EXECUTING = "executing"
    EXECUTION_COMPLETE = "execution_complete"
    IN_PROGRESS = "in_progress"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ValidationStatus(str, Enum):
    POTENTIAL = "potential"
    UNDER_INVESTIGATION = "under_investigation"
    CONFIRMED = "confirmed"
    FALSE_POSITIVE = "false_positive"
    UNABLE_TO_VALIDATE = "unable_to_validate"


class ScopeRef(BaseModel):
    """A structured reference to an authorized target scope."""

    scope_ref: str
    domain: str
    mode: str = "operator_declared"
    operator_id: str
    objective: str
    declared_at: datetime = Field(default_factory=_now)
    allowed_operations: list[str] = Field(
        default_factory=lambda: ["passive_recon", "read_only_analysis"]
    )
    active_testing_authorized: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class FindingRef(BaseModel):
    """A lightweight reference to a finding in the Shared Findings Store."""

    finding_id: str
    task_id: str
    severity: str = "info"
    category: str = "informational"
    summary: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    validation_status: ValidationStatus = ValidationStatus.POTENTIAL


class AgentMessage(BaseModel):
    """The only message type exchanged between agents and the orchestrator."""

    task_id: str
    from_agent: str
    to_agent: str
    objective: str
    task_type: str = "general"
    finding_refs: list[str] = Field(default_factory=list)
    scope_refs: list[str] = Field(default_factory=list)
    execution_status: str = "pending"
    required_next_steps: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    result_summary: str | None = None

    model_config = {"extra": "forbid"}


class ToolOperation(BaseModel):
    """A model for a protected tool request (NOT executable in Phase 1)."""

    request_id: str
    agent_id: str
    task_id: str
    task_type: str = "general"
    capability: str
    target: str
    operation: str
    justification: str
    risk: str = "LOW"
    grant_id: str | None = None
    phase_context: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=_now)

    model_config = {"extra": "forbid"}


class Task(BaseModel):
    """A unit of work created by the orchestrator."""

    id: str
    objective: str
    workflow_id: str = ""
    target_id: str = ""
    execution_id: str = ""
    task_type: str = "general"
    status: TaskStatus = TaskStatus.CREATED
    scope_refs: list[str] = Field(default_factory=list)
    assigned_agent: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    result_summary: str | None = None
    max_iterations: int = 5
    iteration_count: int = 0
    token_budget: int = 0

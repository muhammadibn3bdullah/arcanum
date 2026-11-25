"""Workflow lifecycle states and message contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LifecycleState(str, Enum):
    PENDING = "PENDING"
    DISPATCHED = "DISPATCHED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RUNNING = "RUNNING"
    NEEDS_INPUT = "NEEDS_INPUT"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    WAITING_FOR_AUTHORIZATION = "WAITING_FOR_AUTHORIZATION"
    RESULT_RECEIVED = "RESULT_RECEIVED"
    VERIFYING = "VERIFYING"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_ACCEPTED = "PARTIALLY_ACCEPTED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class MessageType(str, Enum):
    TASK_DISPATCH = "TASK_DISPATCH"
    ACKNOWLEDGMENT = "ACKNOWLEDGMENT"
    CLARIFICATION_REQUEST = "CLARIFICATION_REQUEST"
    CLARIFICATION_RESPONSE = "CLARIFICATION_RESPONSE"
    PROGRESS_UPDATE = "PROGRESS_UPDATE"
    EXECUTION_RESULT = "EXECUTION_RESULT"
    VERIFICATION_RESULT = "VERIFICATION_RESULT"
    FOLLOW_UP = "FOLLOW_UP"


class WorkflowMessage(BaseModel):
    workflow_id: str
    target_id: str
    task_id: str
    execution_id: str
    sender: str
    recipient: str
    message_type: MessageType
    maestro_tab_id: str = ""
    content: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
    model_config = {"extra": "forbid"}

"""Canonical shared domain conversation contracts.

One workflow + one authorized target = one canonical room, shared by all five
agents. Internal Maestro/OpenCode execution sessions are metadata only; they
are NOT additional user-facing conversations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


AGENT_IDS = [
    "hackbot-lead",
    "recon-agent",
    "hunt-agent",
    "validate-agent",
    "report-agent",
]
MODERATOR_AGENT_ID = "hackbot-lead"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RoomMessageType(str, Enum):
    TASK_ASSIGNMENT = "TASK_ASSIGNMENT"
    ACKNOWLEDGMENT = "ACKNOWLEDGMENT"
    CLARIFICATION = "CLARIFICATION"
    CLARIFICATION_RESPONSE = "CLARIFICATION_RESPONSE"
    EXECUTION_UPDATE = "EXECUTION_UPDATE"
    STRUCTURED_RESULT = "STRUCTURED_RESULT"
    REVIEW = "REVIEW"
    CORRECTION_REQUEST = "CORRECTION_REQUEST"
    UPDATED_RESULT = "UPDATED_RESULT"
    VERIFICATION_DECISION = "VERIFICATION_DECISION"
    HANDOFF = "HANDOFF"
    FINAL_REPORT = "FINAL_REPORT"
    PROVIDER_QUOTA = "PROVIDER_QUOTA"


class DomainRoom(BaseModel):
    workflow_id: str
    target_id: str
    canonical_target: str
    room_id: str
    room_name: str
    created_at: datetime = Field(default_factory=_now)
    last_activity_at: datetime = Field(default_factory=_now)
    room_status: str = "active"
    participant_agent_ids: list[str] = Field(default_factory=lambda: list(AGENT_IDS))
    moderator_agent_id: str = MODERATOR_AGENT_ID
    message_store_ref: str = "arcanum.db:canonical_messages"

    model_config = {"extra": "forbid"}


class CanonicalMessage(BaseModel):
    message_id: str
    room_id: str
    workflow_id: str
    target_id: str
    sender_agent_id: str
    sender_display_name: str
    sender_role: str
    recipient_agent_id: str = ""
    message_type: RoomMessageType
    task_id: str = ""
    execution_id: str = ""
    correlation_id: str = ""
    timestamp: datetime = Field(default_factory=_now)
    message_content: str
    evidence_refs: list[str] = Field(default_factory=list)
    delivery_status: str = "published"
    internal_session_id: str = ""

    model_config = {"extra": "forbid"}

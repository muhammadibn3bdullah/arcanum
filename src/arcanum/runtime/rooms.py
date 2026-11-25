"""Canonical shared domain conversation registry and message publishing.

Maestro may keep internal execution sessions. Those are NOT user-facing domain
conversations. Arcanum owns one canonical room per authorized target, and five
agent-facing references that all resolve to that same ``room_id``.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from ..config.settings import Settings
from ..core.rooms import (
    AGENT_IDS,
    MODERATOR_AGENT_ID,
    CanonicalMessage,
    DomainRoom,
    RoomMessageType,
)
from ..memory.database import Database


def _canonical_domain(target_id: str) -> str:
    return str(target_id).strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0].removeprefix("www.")


def _assert_authorized(target_id: str, db: Database) -> str:
    """Resolve authorization from trusted scope records; never guess a target."""
    domain = _canonical_domain(target_id)
    scopes = db.find_scopes_by_domain(domain)
    if not scopes:
        scopes = db.find_scopes_by_domain(str(target_id).strip().lower())
    if not scopes:
        raise PermissionError(
            f"Target '{target_id}' has no authorized scope declaration; "
            "register it through the Scope Manager before creating a room"
        )
    return scopes[0].domain


def get_or_create_domain_room(
    workflow_id: str,
    target_id: str,
    settings: Settings | None = None,
    db: Database | None = None,
) -> DomainRoom:
    """Atomically resolve or create the one canonical room for a target."""
    if not workflow_id or not target_id:
        raise ValueError("workflow_id and target_id are required")
    settings = settings or Settings()
    owns_db = db is None
    db = db or Database(settings.db_path)
    try:
        db.conn.execute("BEGIN IMMEDIATE")
        existing = db.get_room(workflow_id, target_id)
        if existing:
            room = DomainRoom.model_validate(existing)
        else:
            canonical = _assert_authorized(target_id, db)
            room_id = "room_" + hashlib.sha256(
                f"{workflow_id}|{_canonical_domain(target_id)}".encode()
            ).hexdigest()[:16]
            room = DomainRoom(
                workflow_id=workflow_id,
                target_id=target_id,
                canonical_target=canonical,
                room_id=room_id,
                room_name=_canonical_domain(target_id),
            )
            db.save_room(room)
        for agent_id in AGENT_IDS:
            db.set_room_view(agent_id, workflow_id, target_id, room.room_id)
        db.conn.commit()
        return room
    except Exception:
        db.conn.rollback()
        raise
    finally:
        if owns_db:
            db.close()


def get_room_for_agent(
    agent_id: str, workflow_id: str, target_id: str,
    settings: Settings | None = None,
) -> DomainRoom | None:
    """Return the canonical room an agent view resolves to."""
    settings = settings or Settings()
    db = Database(settings.db_path)
    try:
        view_room = db.get_room_view(agent_id, workflow_id, target_id)
        if not view_room:
            return None
        data = db.get_room_by_id(view_room)
        if not data or data.get("workflow_id") != workflow_id or data.get("target_id") != target_id:
            return None
        return DomainRoom.model_validate(data)
    finally:
        db.close()


def publish_message(
    workflow_id: str,
    target_id: str,
    sender_agent_id: str,
    message_type: RoomMessageType,
    content: str,
    *,
    room_id: str = "",
    recipient_agent_id: str = "",
    task_id: str = "",
    execution_id: str = "",
    correlation_id: str = "",
    evidence_refs: list[str] | None = None,
    idempotency_key: str = "",
    internal_session_id: str = "",
    settings: Settings | None = None,
) -> CanonicalMessage:
    """Publish exactly one canonical message into the target's shared room."""
    if sender_agent_id not in AGENT_IDS:
        raise ValueError(f"Unknown agent identity: {sender_agent_id}")
    settings = settings or Settings()
    db = Database(settings.db_path)
    try:
        room = (
            DomainRoom.model_validate(db.get_room_by_id(room_id))
            if room_id
            else get_or_create_domain_room(workflow_id, target_id, settings, db)
        )
        if room.workflow_id != workflow_id or room.target_id != target_id:
            raise ValueError("Room does not belong to this workflow/target")
        key = idempotency_key or str(uuid.uuid4())
        message = CanonicalMessage(
            message_id="msg_" + hashlib.sha256(key.encode()).hexdigest()[:20],
            room_id=room.room_id,
            workflow_id=workflow_id,
            target_id=target_id,
            sender_agent_id=sender_agent_id,
            sender_display_name=sender_agent_id,
            sender_role="moderator" if sender_agent_id == MODERATOR_AGENT_ID else "participant",
            recipient_agent_id=recipient_agent_id,
            message_type=message_type,
            task_id=task_id,
            execution_id=execution_id,
            correlation_id=correlation_id,
            message_content=content,
            evidence_refs=list(evidence_refs or []),
            internal_session_id=internal_session_id,
        )
        db.save_canonical_message(message, key)
        db.touch_room(room.room_id, message.timestamp.isoformat())
        return message
    finally:
        db.close()


def room_messages(
    room_id: str, after_id: str = "", limit: int = 500,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    settings = settings or Settings()
    db = Database(settings.db_path)
    try:
        return db.messages_for_room(room_id, after_id=after_id, limit=limit)
    finally:
        db.close()


def migrate_from_agent_tabs(settings: Settings | None = None) -> dict[str, Any]:
    """Create one canonical room per active C2.1.3 target, preserving tabs."""
    from .tab_registry import TabRegistry

    settings = settings or Settings()
    registry = TabRegistry(settings.tab_registry_path)
    created: list[str] = []
    try:
        for row in registry.active_for_workflow_all():
            workflow_id, target_id = row["workflow_id"], row["target_id"]
            if not target_id:
                continue
            try:
                room = get_or_create_domain_room(workflow_id, target_id, settings)
            except PermissionError:
                continue  # unregistered target: no room until authorized
            created.append(room.room_id)
        return {"rooms": sorted(set(created)), "count": len(set(created))}
    finally:
        registry.close()

"""Append-only audit log for authorization decisions.

Audit records are written to a dedicated SQLite file, separate from the findings
store, and cannot be deleted or modified through this interface.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .permission_models import PermissionAuditRecord


class PermissionAuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS permission_audit (
                record_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                requested_capability TEXT NOT NULL,
                target TEXT NOT NULL,
                decision TEXT NOT NULL,
                decision_reason TEXT NOT NULL,
                grant_id TEXT,
                policy_ref TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def record(self, record: PermissionAuditRecord) -> None:
        self.conn.execute(
            """INSERT INTO permission_audit
               (record_id, event_type, agent_id, task_id, requested_capability,
                target, decision, decision_reason, grant_id, policy_ref, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.record_id,
                record.event_type,
                record.agent_id,
                record.task_id,
                record.requested_capability,
                record.target,
                record.decision,
                record.decision_reason,
                record.grant_id,
                record.policy_ref,
                record.timestamp.isoformat(),
            ),
        )
        self.conn.commit()

    def list_records(
        self, agent_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        if agent_id:
            rows = self.conn.execute(
                "SELECT * FROM permission_audit WHERE agent_id = ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (agent_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM permission_audit ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def denied_capabilities_for_task(
        self, agent_id: str, task_id: str, capabilities: set[str]
    ) -> set[str]:
        """Proxy capabilities previously denied for this agent/task (invariant 12)."""
        rows = self.conn.execute(
            "SELECT requested_capability FROM permission_audit "
            "WHERE agent_id = ? AND task_id = ? AND event_type = 'permission_denied' "
            "ORDER BY timestamp",
            (agent_id, task_id),
        ).fetchall()
        return {r["requested_capability"] for r in rows if r["requested_capability"] in capabilities}

    def close(self) -> None:
        self.conn.close()

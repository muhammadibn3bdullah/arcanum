"""Human-in-the-loop approval queue for DPM permission requests.

When the DPM returns REQUIRE_HUMAN_APPROVAL, the request is recorded here as
pending. Only an operator call to `approve_pending` may turn it into a grant;
the agent never receives a grant from this queue directly.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .permission_models import (
    PermissionDecisionResult,
    PermissionRequest,
    RiskLevel,
)


class ApprovalStatus:
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class ApprovalQueue:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approval_requests (
                approval_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                capability TEXT NOT NULL,
                target TEXT NOT NULL,
                operation TEXT NOT NULL,
                risk TEXT NOT NULL,
                justification TEXT NOT NULL,
                request_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                decided_at TEXT,
                decided_by TEXT,
                decision_reason TEXT
            )
            """
        )
        self.conn.commit()

    def submit(
        self, request: PermissionRequest, result: PermissionDecisionResult
    ) -> str | None:
        """Record a pending approval; return approval_id or None if not required."""
        if result.decision.value != "REQUIRE_HUMAN_APPROVAL":
            return None
        approval_id = f"approval_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO approval_requests
               (approval_id, request_id, agent_id, task_id, capability, target,
                operation, risk, justification, request_json, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                approval_id,
                request.request_id,
                request.agent_id,
                request.task_id,
                request.requested_capability,
                request.target,
                request.operation,
                request.risk.value,
                request.justification,
                request.model_dump_json(),
                ApprovalStatus.PENDING,
                now,
            ),
        )
        self.conn.commit()
        return approval_id

    def get(self, approval_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM approval_requests WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_pending(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        if agent_id:
            rows = self.conn.execute(
                "SELECT * FROM approval_requests WHERE status = ? AND agent_id = ? "
                "ORDER BY created_at",
                (ApprovalStatus.PENDING, agent_id),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM approval_requests WHERE status = ? ORDER BY created_at",
                (ApprovalStatus.PENDING,),
            ).fetchall()
        return [dict(r) for r in rows]

    def decide(
        self, approval_id: str, approved: bool, decided_by: str, reason: str = ""
    ) -> dict[str, Any] | None:
        record = self.get(approval_id)
        if record is None or record["status"] != ApprovalStatus.PENDING:
            return None
        status = ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED
        self.conn.execute(
            """UPDATE approval_requests SET status = ?, decided_at = ?, decided_by = ?,
               decision_reason = ? WHERE approval_id = ?""",
            (status, datetime.now(timezone.utc).isoformat(), decided_by, reason, approval_id),
        )
        self.conn.commit()
        return self.get(approval_id)

    def get_by_request_id(self, request_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM approval_requests WHERE request_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (request_id,),
        ).fetchone()
        return dict(row) if row else None

    def request_from_record(self, record: dict[str, Any]) -> PermissionRequest:
        return PermissionRequest.model_validate_json(record["request_json"])

    def close(self) -> None:
        self.conn.close()

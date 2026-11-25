"""SQLite persistence for tasks, findings, scope declarations, and audit records.

Authorization records are separate from findings (see ARCHITECTURE.md section 7).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..core.models import FindingRef, ScopeRef, Task


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                scope_refs TEXT NOT NULL,
                assigned_agent TEXT,
                dependencies TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                result_summary TEXT,
                max_iterations INTEGER NOT NULL,
                iteration_count INTEGER NOT NULL,
                token_budget INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS findings (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                data TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scope_declarations (
                scope_ref TEXT PRIMARY KEY,
                domain TEXT NOT NULL,
                operator_id TEXT NOT NULL,
                declared_at TEXT NOT NULL,
                data TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                target TEXT NOT NULL,
                detail TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS executions (
                execution_id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                tab_id TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workflow_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                execution_id TEXT NOT NULL,
                sender TEXT NOT NULL,
                recipient TEXT NOT NULL,
                message_type TEXT NOT NULL,
                content TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS lifecycle_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                execution_id TEXT NOT NULL,
                from_state TEXT NOT NULL,
                to_state TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS maestro_callbacks (
                callback_id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                execution_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                specialist_tab_id TEXT NOT NULL,
                lead_tab_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'created',
                data TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS domain_rooms (
                room_id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                canonical_target TEXT NOT NULL,
                room_name TEXT NOT NULL,
                room_status TEXT NOT NULL DEFAULT 'active',
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_activity_at TEXT NOT NULL,
                UNIQUE (workflow_id, target_id)
            );

            CREATE TABLE IF NOT EXISTS room_agent_views (
                agent_id TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                room_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (agent_id, workflow_id, target_id)
            );

            CREATE TABLE IF NOT EXISTS canonical_messages (
                message_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                room_id TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                sender_agent_id TEXT NOT NULL,
                message_type TEXT NOT NULL,
                task_id TEXT NOT NULL DEFAULT '',
                execution_id TEXT NOT NULL DEFAULT '',
                timestamp TEXT NOT NULL,
                content TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS canonical_messages_room_order
                ON canonical_messages (room_id, timestamp);
            """
        )
        # Backward-compatible column migration for existing databases.
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(tasks)").fetchall()}
        for column in ("workflow_id", "target_id", "execution_id"):
            if column not in cols:
                self.conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        self.conn.commit()

    def audit(
        self, event_type: str, actor: str, target: str, detail: str, timestamp: str
    ) -> None:
        self.conn.execute(
            "INSERT INTO audit_log (event_type, actor, target, detail, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (event_type, actor, target, detail, timestamp),
        )
        self.conn.commit()

    def save_task(self, task: Task) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO tasks
               (id, objective, workflow_id, target_id, execution_id, status,
                scope_refs, assigned_agent, dependencies,
                created_at, updated_at, result_summary, max_iterations,
                iteration_count, token_budget)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.id,
                task.objective,
                task.workflow_id,
                task.target_id,
                task.execution_id,
                task.status.value,
                json.dumps(task.scope_refs),
                task.assigned_agent,
                json.dumps(task.dependencies),
                task.created_at.isoformat(),
                task.updated_at.isoformat(),
                task.result_summary,
                task.max_iterations,
                task.iteration_count,
                task.token_budget,
            ),
        )
        self.conn.commit()

    def get_task(self, task_id: str) -> Task | None:
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        return Task(
            id=row["id"],
            objective=row["objective"],
            workflow_id=row["workflow_id"] or "",
            target_id=row["target_id"] or "",
            execution_id=row["execution_id"] or "",
            status=row["status"],
            scope_refs=json.loads(row["scope_refs"]),
            assigned_agent=row["assigned_agent"],
            dependencies=json.loads(row["dependencies"]),
            created_at=datetime_fromiso(row["created_at"]),
            updated_at=datetime_fromiso(row["updated_at"]),
            result_summary=row["result_summary"],
            max_iterations=row["max_iterations"],
            iteration_count=row["iteration_count"],
            token_budget=row["token_budget"],
        )

    def list_tasks(self) -> list[Task]:
        rows = self.conn.execute("SELECT id FROM tasks ORDER BY created_at").fetchall()
        out = []
        for r in rows:
            task = self.get_task(r["id"])
            if task:
                out.append(task)
        return out

    def save_scope(self, scope: ScopeRef) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO scope_declarations
               (scope_ref, domain, operator_id, declared_at, data)
               VALUES (?, ?, ?, ?, ?)""",
            (
                scope.scope_ref,
                scope.domain,
                scope.operator_id,
                scope.declared_at.isoformat(),
                scope.model_dump_json(),
            ),
        )
        self.conn.commit()

    def get_scope(self, scope_ref: str) -> ScopeRef | None:
        row = self.conn.execute(
            "SELECT data FROM scope_declarations WHERE scope_ref = ?", (scope_ref,)
        ).fetchone()
        return ScopeRef.model_validate_json(row["data"]) if row else None

    def find_scopes_by_domain(self, domain: str) -> list[ScopeRef]:
        needle = str(domain).strip().lower().removeprefix("https://").removeprefix("http://")
        needle = needle.split("/")[0].removeprefix("www.")
        rows = self.conn.execute(
            "SELECT data FROM scope_declarations WHERE lower(domain)=?", (needle,)
        ).fetchall()
        return [ScopeRef.model_validate_json(r["data"]) for r in rows]

    def save_finding(self, finding: FindingRef) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO findings (id, task_id, data) VALUES (?, ?, ?)",
            (finding.finding_id, finding.task_id, finding.model_dump_json()),
        )
        self.conn.commit()

    def save_execution(
        self,
        execution_id: str,
        workflow_id: str,
        target_id: str,
        task_id: str,
        agent_id: str,
        tab_id: str,
        data: dict[str, Any],
    ) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO executions
               (execution_id, workflow_id, target_id, task_id, agent_id, tab_id,
                data, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                execution_id,
                workflow_id,
                target_id,
                task_id,
                agent_id,
                tab_id,
                json.dumps(data),
            ),
        )
        self.conn.commit()

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT data FROM executions WHERE execution_id = ?", (execution_id,)
        ).fetchone()
        return json.loads(row["data"]) if row else None

    def save_message(
        self, workflow_id: str, target_id: str, task_id: str, execution_id: str,
        sender: str, recipient: str, message_type: str, content: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO workflow_messages
               (workflow_id, target_id, task_id, execution_id, sender, recipient,
                message_type, content, data, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (workflow_id, target_id, task_id, execution_id, sender, recipient,
             message_type, content, json.dumps(data or {})),
        )
        self.conn.commit()

    def messages_for_execution(self, execution_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM workflow_messages WHERE execution_id = ? ORDER BY id",
            (execution_id,),
        ).fetchall()
        return [
            {
                "workflow_id": r["workflow_id"], "target_id": r["target_id"],
                "task_id": r["task_id"], "execution_id": r["execution_id"],
                "sender": r["sender"], "recipient": r["recipient"],
                "message_type": r["message_type"], "content": r["content"],
                "data": json.loads(r["data"]), "created_at": r["created_at"],
            }
            for r in rows
        ]

    def save_callback(
        self,
        callback_id: str,
        workflow_id: str,
        target_id: str,
        task_id: str,
        execution_id: str,
        agent_id: str,
        specialist_tab_id: str,
        lead_tab_id: str,
        status: str = "created",
        data: dict[str, Any] | None = None,
    ) -> None:
        """Persist full correlation metadata for a Maestro completion callback."""
        self.conn.execute(
            """INSERT OR REPLACE INTO maestro_callbacks
               (callback_id, workflow_id, target_id, task_id, execution_id,
                agent_id, specialist_tab_id, lead_tab_id, status, data, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                callback_id, workflow_id, target_id, task_id, execution_id,
                agent_id, specialist_tab_id, lead_tab_id, status,
                json.dumps(data or {}),
            ),
        )
        self.conn.commit()

    def get_callback(self, callback_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM maestro_callbacks WHERE callback_id=?", (callback_id,)
        ).fetchone()
        if not row:
            return None
        return {
            "callback_id": row["callback_id"],
            "workflow_id": row["workflow_id"],
            "target_id": row["target_id"],
            "task_id": row["task_id"],
            "execution_id": row["execution_id"],
            "agent_id": row["agent_id"],
            "specialist_tab_id": row["specialist_tab_id"],
            "lead_tab_id": row["lead_tab_id"],
            "status": row["status"],
            "data": json.loads(row["data"]),
        }

    # ------------------------------------------------------------------
    # Canonical shared domain rooms (C2.1.4)
    # ------------------------------------------------------------------
    def save_room(self, room: Any) -> None:
        data = room.model_dump(mode="json")
        self.conn.execute(
            """INSERT OR IGNORE INTO domain_rooms
               (room_id, workflow_id, target_id, canonical_target, room_name,
                room_status, data, created_at, last_activity_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                room.room_id, room.workflow_id, room.target_id,
                room.canonical_target, room.room_name, room.room_status,
                json.dumps(data), room.created_at.isoformat(),
                room.last_activity_at.isoformat(),
            ),
        )
        self.conn.commit()

    def get_room(self, workflow_id: str, target_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT data FROM domain_rooms WHERE workflow_id=? AND target_id=?",
            (workflow_id, target_id),
        ).fetchone()
        return json.loads(row["data"]) if row else None

    def get_room_by_id(self, room_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT data FROM domain_rooms WHERE room_id=?", (room_id,)
        ).fetchone()
        return json.loads(row["data"]) if row else None

    def list_rooms(self, workflow_id: str = "") -> list[dict[str, Any]]:
        if workflow_id:
            rows = self.conn.execute(
                "SELECT data FROM domain_rooms WHERE workflow_id=? ORDER BY target_id",
                (workflow_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT data FROM domain_rooms ORDER BY workflow_id, target_id"
            ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def touch_room(self, room_id: str, timestamp: str) -> None:
        self.conn.execute(
            "UPDATE domain_rooms SET last_activity_at=? WHERE room_id=?",
            (timestamp, room_id),
        )
        self.conn.commit()

    def set_room_view(
        self, agent_id: str, workflow_id: str, target_id: str, room_id: str
    ) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO room_agent_views
               (agent_id, workflow_id, target_id, room_id, created_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (agent_id, workflow_id, target_id, room_id),
        )
        self.conn.commit()

    def get_room_view(
        self, agent_id: str, workflow_id: str, target_id: str
    ) -> str:
        row = self.conn.execute(
            """SELECT room_id FROM room_agent_views
               WHERE agent_id=? AND workflow_id=? AND target_id=?""",
            (agent_id, workflow_id, target_id),
        ).fetchone()
        return str(row["room_id"]) if row else ""

    def room_views(self, room_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM room_agent_views WHERE room_id=? ORDER BY agent_id",
            (room_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def save_canonical_message(self, message: Any, idempotency_key: str) -> bool:
        """Insert one authoritative message; return False if already present."""
        info = self.conn.execute(
            """INSERT OR IGNORE INTO canonical_messages
               (message_id, idempotency_key, room_id, workflow_id, target_id,
                sender_agent_id, message_type, task_id, execution_id,
                timestamp, content, data, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                message.message_id, idempotency_key, message.room_id,
                message.workflow_id, message.target_id, message.sender_agent_id,
                message.message_type.value, message.task_id, message.execution_id,
                message.timestamp.isoformat(), message.message_content,
                json.dumps(message.model_dump(mode="json")),
            ),
        )
        self.conn.commit()
        return info.rowcount == 1

    def messages_for_room(
        self, room_id: str, after_id: str = "", limit: int = 500
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT data FROM canonical_messages
               WHERE room_id=? AND message_id>? ORDER BY rowid LIMIT ?""",
            (room_id, after_id, limit),
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def transition_state(
        self, workflow_id: str, target_id: str, task_id: str, execution_id: str,
        from_state: str, to_state: str, reason: str,
    ) -> None:
        self.conn.execute(
            """INSERT INTO lifecycle_events
               (workflow_id, target_id, task_id, execution_id, from_state,
                to_state, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (workflow_id, target_id, task_id, execution_id, from_state, to_state, reason),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


def datetime_fromiso(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)

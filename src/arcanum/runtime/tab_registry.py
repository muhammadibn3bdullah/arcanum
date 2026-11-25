"""Persistent Maestro tab registry — one uniform key for all five agents.

Identity model (C2.1.3)::

    (workflow_id, target_id, agent_id) -> tab_id

`hackbot-lead` is target-scoped exactly like the four specialists. There is no
global Lead tab and no scope/type distinction in the active table. Every reused
tab is validated against trusted Maestro session metadata before use. A stale
mapping is marked stale and replaced exactly once for the same key.

Migration: an existing v1 table (which had a `scope` column and a workflow-only
Lead key) is archived to `maestro_tabs_legacy_v1`. Specialist mappings are
preserved. A legacy global Lead tab is reused only when the workflow has exactly
one target; it is never silently assigned to multiple targets.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from . import maestro

SCHEMA_VERSION = 2


class TabRegistry:
    def __init__(self, db_path: Path | str):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._migrate()
        self.conn.commit()

    # ------------------------------------------------------------------
    # Schema / migration
    # ------------------------------------------------------------------
    def _meta_version(self) -> int:
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_meta (name TEXT PRIMARY KEY, value INTEGER NOT NULL)"
        )
        row = self.conn.execute(
            "SELECT value FROM schema_meta WHERE name='tab_registry_version'"
        ).fetchone()
        return int(row["value"]) if row else 0

    def _set_meta_version(self, version: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO schema_meta(name, value) VALUES ('tab_registry_version', ?)",
            (version,),
        )

    def _migrate(self) -> None:
        if self._meta_version() >= SCHEMA_VERSION:
            self._ensure_tables()
            return

        cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(maestro_tabs)").fetchall()
        }
        if cols and "scope" in cols:
            # Archive v1 rows; never delete historical mappings.
            self.conn.execute("DROP TABLE IF EXISTS maestro_tabs_legacy_v1")
            self.conn.execute("ALTER TABLE maestro_tabs RENAME TO maestro_tabs_legacy_v1")

        self._ensure_tables()

        if self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='maestro_tabs_legacy_v1'"
        ).fetchone():
            self._copy_legacy_rows()

        self._set_meta_version(SCHEMA_VERSION)

    def _ensure_tables(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS maestro_tabs (
                workflow_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                tab_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                last_task_id TEXT NOT NULL DEFAULT '',
                last_execution_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (workflow_id, target_id, agent_id)
            );
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tab_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                action TEXT NOT NULL,
                tab_id TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

    def _copy_legacy_rows(self) -> None:
        # Specialist rows map directly into the uniform key.
        self.conn.execute(
            """INSERT OR IGNORE INTO maestro_tabs
               (workflow_id, target_id, agent_id, tab_id, status,
                last_task_id, last_execution_id, created_at, last_used_at)
               SELECT workflow_id, target_id, agent_id, tab_id, status,
                      last_task_id, last_execution_id, created_at, last_used_at
               FROM maestro_tabs_legacy_v1
               WHERE scope='specialist' AND target_id<>''"""
        )
        # A legacy global Lead tab is preserved only for workflows with exactly
        # one target. It is never reused for multiple targets.
        rows = self.conn.execute(
            """SELECT workflow_id, tab_id, status, last_task_id, last_execution_id,
                      created_at, last_used_at
               FROM maestro_tabs_legacy_v1 WHERE scope='lead'"""
        ).fetchall()
        for row in rows:
            targets = [
                r["target_id"]
                for r in self.conn.execute(
                    "SELECT DISTINCT target_id FROM maestro_tabs_legacy_v1 "
                    "WHERE workflow_id=? AND scope='specialist' AND target_id<>''",
                    (row["workflow_id"],),
                ).fetchall()
            ]
            if len(targets) == 1:
                self.conn.execute(
                    """INSERT OR IGNORE INTO maestro_tabs
                       (workflow_id, target_id, agent_id, tab_id, status,
                        last_task_id, last_execution_id, created_at, last_used_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        row["workflow_id"], targets[0], maestro.HEAD_AGENT,
                        row["tab_id"], row["status"], row["last_task_id"],
                        row["last_execution_id"], row["created_at"], row["last_used_at"],
                    ),
                )
                reason = f"legacy Lead tab preserved for sole target {targets[0]}"
            else:
                reason = (
                    f"legacy Lead tab archived; {len(targets)} targets found, "
                    "target-specific Lead tabs will be created lazily"
                )
            self.conn.execute(
                """INSERT INTO tab_events
                   (workflow_id, target_id, agent_id, action, tab_id, reason, created_at)
                   VALUES (?, '', ?, 'lead_migration', ?, ?, CURRENT_TIMESTAMP)""",
                (row["workflow_id"], maestro.HEAD_AGENT, row["tab_id"], reason),
            )

    # ------------------------------------------------------------------
    # Uniform key helpers
    # ------------------------------------------------------------------
    @staticmethod
    def agent_key(workflow_id: str, target_id: str, agent_id: str) -> tuple[str, str, str]:
        return (workflow_id, target_id, agent_id)

    @staticmethod
    def lead_key(workflow_id: str, target_id: str) -> tuple[str, str, str]:
        return (workflow_id, target_id, maestro.HEAD_AGENT)

    # ------------------------------------------------------------------
    # Generic storage — used identically for all five agents
    # ------------------------------------------------------------------
    def get(self, workflow_id: str, target_id: str, agent_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM maestro_tabs WHERE workflow_id=? AND target_id=? AND agent_id=?",
            (workflow_id, target_id, agent_id),
        ).fetchone()
        return dict(row) if row else None

    def put(
        self, workflow_id: str, target_id: str, agent_id: str, tab_id: str,
        task_id: str = "", execution_id: str = "",
    ) -> None:
        self.conn.execute(
            """INSERT INTO maestro_tabs
               (workflow_id, target_id, agent_id, tab_id, status,
                last_task_id, last_execution_id, created_at, last_used_at)
               VALUES (?, ?, ?, ?, 'active', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(workflow_id, target_id, agent_id) DO UPDATE SET
                 tab_id=excluded.tab_id, status='active',
                 last_task_id=excluded.last_task_id,
                 last_execution_id=excluded.last_execution_id,
                 last_used_at=CURRENT_TIMESTAMP""",
            (workflow_id, target_id, agent_id, tab_id, task_id, execution_id),
        )
        self.conn.commit()

    def touch(
        self, workflow_id: str, target_id: str, agent_id: str,
        task_id: str = "", execution_id: str = "",
    ) -> None:
        self.conn.execute(
            """UPDATE maestro_tabs
               SET last_used_at=CURRENT_TIMESTAMP,
                   last_task_id=CASE WHEN ?='' THEN last_task_id ELSE ? END,
                   last_execution_id=CASE WHEN ?='' THEN last_execution_id ELSE ? END
               WHERE workflow_id=? AND target_id=? AND agent_id=?""",
            (task_id, task_id, execution_id, execution_id,
             workflow_id, target_id, agent_id),
        )
        self.conn.commit()

    def mark_stale(self, workflow_id: str, target_id: str, agent_id: str) -> None:
        self.conn.execute(
            "UPDATE maestro_tabs SET status='stale' "
            "WHERE workflow_id=? AND target_id=? AND agent_id=?",
            (workflow_id, target_id, agent_id),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Named helpers (same storage, clearer call sites)
    # ------------------------------------------------------------------
    def get_lead(self, workflow_id: str, target_id: str) -> dict[str, Any] | None:
        return self.get(workflow_id, target_id, maestro.HEAD_AGENT)

    def put_lead(self, workflow_id: str, target_id: str, tab_id: str) -> None:
        self.put(workflow_id, target_id, maestro.HEAD_AGENT, tab_id)

    def get_specialist(self, workflow_id: str, target_id: str, agent_id: str) -> dict[str, Any] | None:
        return self.get(workflow_id, target_id, agent_id)

    def put_specialist(
        self, workflow_id: str, target_id: str, agent_id: str, tab_id: str,
        task_id: str = "", execution_id: str = "",
    ) -> None:
        self.put(workflow_id, target_id, agent_id, tab_id, task_id, execution_id)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate(self, mapping: dict[str, Any], expected_agent: str) -> bool:
        if mapping.get("status") != "active":
            return False
        tab_id = str(mapping.get("tab_id") or "")
        if not tab_id:
            return False
        try:
            tab = maestro.resolve_tab(tab_id)
        except Exception:
            return False
        if str(tab.get("agentName") or "") != expected_agent:
            return False
        return bool(tab.get("tabId") == tab_id)

    # ------------------------------------------------------------------
    # Workflow-wide introspection
    # ------------------------------------------------------------------
    def all_for_workflow(self, workflow_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM maestro_tabs WHERE workflow_id=? "
            "ORDER BY target_id, agent_id",
            (workflow_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def active_for_workflow(self, workflow_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM maestro_tabs WHERE workflow_id=? AND status='active' "
            "ORDER BY target_id, agent_id",
            (workflow_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def active_for_workflow_all(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM maestro_tabs WHERE status='active' AND target_id<>'' "
            "ORDER BY workflow_id, target_id, agent_id"
        ).fetchall()
        return [dict(r) for r in rows]

    def count_for_workflow(self, workflow_id: str, status: str | None = None) -> int:
        if status is None:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM maestro_tabs WHERE workflow_id=?",
                (workflow_id,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM maestro_tabs WHERE workflow_id=? AND status=?",
                (workflow_id, status),
            ).fetchone()
        return int(row["n"])

    def tab_counts_by_agent(self, workflow_id: str) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT agent_id, COUNT(*) AS n FROM maestro_tabs "
            "WHERE workflow_id=? AND status='active' GROUP BY agent_id",
            (workflow_id,),
        ).fetchall()
        return {str(r["agent_id"]): int(r["n"]) for r in rows}

    def targets_for_workflow(self, workflow_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT target_id FROM maestro_tabs WHERE workflow_id=? "
            "AND target_id<>'' ORDER BY target_id",
            (workflow_id,),
        ).fetchall()
        return [str(r["target_id"]) for r in rows]

    # ------------------------------------------------------------------
    # Audit events
    # ------------------------------------------------------------------
    def log_event(
        self, workflow_id: str, target_id: str, agent_id: str,
        action: str, tab_id: str = "", reason: str = "",
    ) -> None:
        self.conn.execute(
            """INSERT INTO tab_events
               (workflow_id, target_id, agent_id, action, tab_id, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (workflow_id, target_id, agent_id, action, tab_id, reason),
        )
        self.conn.commit()

    def events_for_workflow(self, workflow_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM tab_events WHERE workflow_id=? ORDER BY id",
            (workflow_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self.conn.close()

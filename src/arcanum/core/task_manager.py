"""Task manager with deterministic per-domain engagement provisioning."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..config.settings import Settings
from ..core.models import ScopeRef, Task, TaskStatus
from ..memory.database import Database
from ..security.scope_manager import ScopeManager

if TYPE_CHECKING:
    from ..permissions.permission_manager import DynamicPermissionManager


class TaskManager:
    TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.BLOCKED}

    def __init__(
        self,
        db: Database,
        scope_manager: ScopeManager,
        settings: Settings,
        dpm: "DynamicPermissionManager | None" = None,
    ):
        self.db = db
        self.scope = scope_manager
        self.settings = settings
        self.dpm = dpm

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def provision_engagement(self, scope: ScopeRef) -> str:
        """Create ~/Targets/<domain>/ with the standard engagement template."""
        assert self.settings.targets_dir is not None
        folder = self.settings.targets_dir / scope.domain
        (folder / "findings").mkdir(parents=True, exist_ok=True)
        (folder / "evidence").mkdir(parents=True, exist_ok=True)

        (folder / "scope.md").write_text(
            f"# Scope — {scope.domain}\n\n"
            f"- scope_ref: `{scope.scope_ref}`\n"
            f"- declared_by: `{scope.operator_id}`\n"
            f"- declared_at: {scope.declared_at.isoformat()}\n"
            f"- mode: `{scope.mode}`\n"
            f"- objective: {scope.objective}\n"
            "- in_scope: "
            f"`{scope.domain}`\n"
            "- out_of_scope: all other domains and third-party assets\n"
            "- allowed_operations: "
            + ", ".join(f"`{op}`" for op in scope.allowed_operations)
            + "\n- active_testing_authorized: **false** (DPM grant required)\n",
            encoding="utf-8",
        )
        (folder / "notes.md").write_text(
            f"# Notes — {scope.domain}\n\nNo work recorded yet.\n", encoding="utf-8"
        )
        (folder / "submissions.txt").write_text(
            "# UUID\tseverity\tclass\ttitle\n", encoding="utf-8"
        )
        self.db.audit(
            "engagement_provisioned",
            scope.operator_id,
            scope.domain,
            f"folder={folder}",
            self._now_iso(),
        )
        return str(folder)

    def create_task(self, objective: str, operator_id: str, task_type: str = "general") -> Task:
        """Create a task, register+validate scope, and provision folders."""
        refs = self.scope.register_targets(objective, operator_id)
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        task = Task(
            id=task_id,
            objective=objective,
            task_type=task_type,
            status=TaskStatus.BLOCKED if not refs else TaskStatus.CREATED,
            scope_refs=[r.scope_ref for r in refs],
            max_iterations=self.settings.max_iterations,
            token_budget=self.settings.token_budget,
            result_summary=(
                "No domain found in objective; no engagement folder created."
                if not refs
                else None
            ),
        )
        for ref in refs:
            self.provision_engagement(ref)
        if refs:
            task.status = TaskStatus.AUTHORIZED
        self.db.save_task(task)
        self.db.audit(
            "task_created",
            operator_id,
            task_id,
            f"scope_refs={task.scope_refs} status={task.status.value}",
            self._now_iso(),
        )
        return task

    def list_tasks(self) -> list[Task]:
        return self.db.list_tasks()

    def get_task(self, task_id: str) -> Task | None:
        return self.db.get_task(task_id)

    def update_status(self, task_id: str, status: TaskStatus) -> Task | None:
        task = self.get_task(task_id)
        if task is None:
            return None
        task.status = status
        task.updated_at = datetime.now(timezone.utc)
        self.db.save_task(task)
        if status in self.TERMINAL_STATUSES and self.dpm is not None:
            revoked = self.dpm.revoke_for_task(task_id)
            self.db.audit(
                "grants_revoked_on_task_end",
                "dpm",
                task_id,
                f"status={status.value} revoked={revoked}",
                self._now_iso(),
            )
        return task

    def set_result(self, task_id: str, summary: str) -> Task | None:
        task = self.get_task(task_id)
        if task is None:
            return None
        task.result_summary = summary
        task.updated_at = datetime.now(timezone.utc)
        self.db.save_task(task)
        return task

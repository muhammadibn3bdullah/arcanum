from pathlib import Path

import pytest

from arcanum.agents.base_agent import BaseAgent
from arcanum.config.settings import Settings
from arcanum.core.models import TaskStatus
from arcanum.core.orchestrator import Orchestrator
from arcanum.core.task_manager import TaskManager
from arcanum.memory.database import Database
from arcanum.security.scope_manager import ScopeManager


def make_stack(tmp_path: Path, mode: str = "operator_declared", allowlist=None):
    settings = Settings(
        base_dir=tmp_path,
        db_path=tmp_path / "arcanum.db",
        targets_dir=tmp_path / "Targets",
        dynamic_target_mode=mode,
        allowed_operators=["local_operator"],
        target_allowlist=allowlist or [],
    )
    db = Database(settings.db_path)
    scope = ScopeManager(db, settings)
    return settings, db, scope, TaskManager(db, scope, settings)


def test_create_task_provisions_folder_for_declared_domain(tmp_path):
    settings, db, scope, tm = make_stack(tmp_path)
    task = tm.create_task("Assess example.com", "local_operator")
    assert task.status is TaskStatus.AUTHORIZED
    assert len(task.scope_refs) == 1
    folder = settings.targets_dir / "example.com"
    assert folder.is_dir()
    for name in ["scope.md", "notes.md", "submissions.txt"]:
        assert (folder / name).is_file()
    assert (folder / "findings").is_dir()
    assert (folder / "evidence").is_dir()
    assert "active_testing_authorized: **false**" in (folder / "scope.md").read_text(encoding="utf-8")
    assert tm.get_task(task.id) is not None
    assert len(tm.list_tasks()) == 1


def test_non_allowlisted_operator_is_denied(tmp_path):
    _, db, scope, tm = make_stack(tmp_path)
    with pytest.raises(PermissionError):
        tm.create_task("Assess example.com", "attacker")
    assert tm.list_tasks() == []


def test_allowlist_mode_rejects_unlisted_domain(tmp_path):
    _, db, scope, tm = make_stack(
        tmp_path, mode="TARGET_ALLOWLIST_ONLY", allowlist=["allowed.test"]
    )
    with pytest.raises(PermissionError):
        tm.create_task("Assess not-allowed.test", "local_operator")
    task = tm.create_task("Assess allowed.test", "local_operator")
    assert task.status is TaskStatus.AUTHORIZED


def test_no_domain_creates_blocked_task(tmp_path):
    _, db, scope, tm = make_stack(tmp_path)
    task = tm.create_task("Say hello", "local_operator")
    assert task.status is TaskStatus.BLOCKED
    assert task.scope_refs == []


def test_orchestrator_dispatch_sends_only_structured_message(tmp_path):
    settings, db, scope, tm = make_stack(tmp_path)
    task = tm.create_task("Assess example.com", "local_operator")
    agent = BaseAgent("recon_agent", "recon")
    orch = Orchestrator(tm)
    orch.register_agent(agent)
    result = orch.dispatch(task, "recon_agent")
    assert result.to_agent == "orchestrator"
    assert result.execution_status == "acknowledged"
    assert result.result_summary is None
    assert agent.capability_requests == []
    assert tm.get_task(task.id).result_summary is None
    assert tm.get_task(task.id).status.value == "dispatched"

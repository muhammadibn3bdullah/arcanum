from pathlib import Path

from arcanum.config.settings import Settings
from arcanum.core.models import TaskStatus
from arcanum.core.orchestrator import Orchestrator
from arcanum.core.task_manager import TaskManager
from arcanum.memory.database import Database
from arcanum.permissions.approval_queue import ApprovalQueue
from arcanum.permissions.authorization_gateway import AuthorizationGateway
from arcanum.permissions.permission_audit import PermissionAuditLog
from arcanum.permissions.permission_manager import DynamicPermissionManager, PermissionStore
from arcanum.permissions.permission_models import (
    PermissionDecision,
    PermissionRequest,
    RiskLevel,
)
from arcanum.permissions.permission_policy import PermissionPolicy, PolicyConfig
from arcanum.security.scope_manager import ScopeManager


def make_dpm(tmp_path, policy=None):
    queue = ApprovalQueue(tmp_path / "approvals.sqlite")
    # Explicit HITL policy: MEDIUM requires approval so the queue is testable.
    if policy is None:
        from arcanum.permissions.permission_policy import PolicyConfig
        policy = PermissionPolicy(PolicyConfig(allow_auto_medium=False))
    dpm = DynamicPermissionManager(
        store=PermissionStore(tmp_path / "grants.db"),
        audit=PermissionAuditLog(tmp_path / "audit.db"),
        policy=policy,
        approval_queue=queue,
        scope_verifier=lambda target, operation="": True,
    )
    return dpm, queue


def request(agent="recon_agent", capability="http_read", risk=RiskLevel.MEDIUM,
            task_id="task_1"):
    return PermissionRequest(
        request_id="req_hitl", agent_id=agent, task_id=task_id,
        requested_capability=capability, target="lab_app", operation="read",
        justification="operator approval test", risk=risk,
    )


def test_medium_risk_request_queues_for_human(tmp_path):
    dpm, queue = make_dpm(tmp_path)
    result = dpm.request_permission(request(), task_authorized=True)
    assert result.decision == PermissionDecision.REQUIRE_HUMAN_APPROVAL
    assert result.grant is None
    pending = queue.list_pending()
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"


def test_operator_approval_creates_grant(tmp_path):
    dpm, queue = make_dpm(tmp_path)
    dpm.request_permission(request(), task_authorized=True)
    approval_id = queue.list_pending()[0]["approval_id"]
    approved = dpm.approve_pending(approval_id, decided_by="local_operator")
    assert approved.decision == PermissionDecision.ALLOW
    assert approved.grant is not None
    assert queue.get(approval_id)["status"] == "approved"


def test_operator_denial_creates_no_grant(tmp_path):
    dpm, queue = make_dpm(tmp_path)
    dpm.request_permission(request(), task_authorized=True)
    approval_id = queue.list_pending()[0]["approval_id"]
    dpm.deny_pending(approval_id, decided_by="local_operator", reason="not authorized")
    record = queue.get(approval_id)
    assert record["status"] == "denied"
    assert record["decision_reason"] == "not authorized"
    assert dpm.list_grants() == []


def test_always_denied_never_queues(tmp_path):
    dpm, queue = make_dpm(tmp_path, PermissionPolicy(PolicyConfig(always_denied={"http_read"})))
    result = dpm.request_permission(request(), task_authorized=True)
    assert result.decision == PermissionDecision.DENY
    assert queue.list_pending() == []


def test_task_completion_revokes_grants(tmp_path):
    dpm, queue = make_dpm(tmp_path)
    s = Settings(base_dir=tmp_path, db_path=tmp_path / "db.sqlite",
                 targets_dir=tmp_path / "Targets", allowed_operators=["op"])
    db = Database(s.db_path)
    tm = TaskManager(db, ScopeManager(db, s), s, dpm=dpm)
    task = tm.create_task("Assess example.com", "op")
    grant = dpm.request_permission(
        PermissionRequest(
            request_id="req_lifecycle", agent_id="recon_agent", task_id=task.id,
            requested_capability="http_read", target="example.com", operation="read",
            justification="test", risk=RiskLevel.LOW,
        ),
        task_authorized=True,
    ).grant
    assert grant is not None
    tm.update_status(task.id, TaskStatus.COMPLETED)
    valid, reason, _ = dpm.validate_grant(grant.grant_id, "recon_agent", task.id,
                                          "http_read", "example.com", "read")
    assert valid is False
    assert "revoked" in reason.lower()


def test_agent_unregister_revokes_grants(tmp_path):
    dpm, queue = make_dpm(tmp_path)
    s = Settings(base_dir=tmp_path, db_path=tmp_path / "db.sqlite",
                 targets_dir=tmp_path / "Targets", allowed_operators=["op"])
    db = Database(s.db_path)
    orch = Orchestrator(TaskManager(db, ScopeManager(db, s), s), dpm=dpm)
    grant = dpm.request_permission(request(), task_authorized=True).grant
    # HIGH is queued, so use a LOW grant for this lifecycle check.
    if grant is None:
        grant = dpm.request_permission(
            request(risk=RiskLevel.LOW), task_authorized=True
        ).grant
    orch.unregister_agent("recon_agent")
    valid, _, _ = dpm.validate_grant(grant.grant_id, "recon_agent", "task_1",
                                     "http_read", "lab_app", "read")
    assert valid is False

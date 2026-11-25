from datetime import datetime, timedelta, timezone

import pytest

from arcanum.permissions.authorization_gateway import AuthorizationGateway
from arcanum.permissions.permission_audit import PermissionAuditLog
from arcanum.permissions.permission_manager import DynamicPermissionManager, PermissionStore
from arcanum.permissions.permission_models import (
    AuthorizationGrant,
    DEFAULT_PROFILES,
    PermissionDecision,
    PermissionRequest,
    RiskLevel,
)
from arcanum.permissions.permission_policy import (
    PROXY_CAPABILITIES,
    PermissionPolicy,
    PolicyConfig,
)


def make_dpm(tmp_path, policy=None, profiles=None, scope_ok=True):
    store = PermissionStore(tmp_path / "grants.db")
    audit = PermissionAuditLog(tmp_path / "audit.db")
    dpm = DynamicPermissionManager(
        store=store,
        audit=audit,
        policy=policy or PermissionPolicy(),
        profiles=profiles if profiles is not None else DEFAULT_PROFILES,
        scope_verifier=lambda _: scope_ok,
    )
    return dpm, store, audit


def make_request(agent_id="recon_agent", capability="http_read", risk=RiskLevel.LOW,
                 task_id="task_1", target="lab_app", operation="read",
                 duration_seconds=60, task_type="general", request_id="req_1", **extra):
    return PermissionRequest(
        request_id=request_id,
        agent_id=agent_id,
        task_id=task_id,
        requested_capability=capability,
        target=target,
        operation=operation,
        justification="test",
        duration_seconds=duration_seconds,
        risk=risk,
        task_type=task_type,
        **extra,
    )


def test_unregistered_agent_denied(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    req = make_request(agent_id="ghost")
    result = dpm.request_permission(req, task_authorized=True)
    assert result.decision == PermissionDecision.DENY
    assert "no registered permission profile" in result.reason


def test_capability_not_in_profile_denied(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    req = make_request(capability="shell_exec")
    result = dpm.request_permission(req, task_authorized=True)
    assert result.decision == PermissionDecision.DENY
    assert "not in the maximum profile" in result.reason


def test_unauthorized_task_denied(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    req = make_request()
    result = dpm.request_permission(req, task_authorized=False)
    assert result.decision == PermissionDecision.DENY
    assert "Task" in result.reason and "not authorized" in result.reason


def test_unverified_scope_denied(tmp_path):
    dpm, *_ = make_dpm(tmp_path, scope_ok=False)
    req = make_request()
    result = dpm.request_permission(req, task_authorized=True)
    assert result.decision == PermissionDecision.DENY
    assert "not within the approved task scope" in result.reason


def test_critical_auto_approved(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    req = make_request(risk=RiskLevel.CRITICAL)
    result = dpm.request_permission(req, task_authorized=True)
    assert result.decision == PermissionDecision.ALLOW
    assert result.grant is not None
    assert result.requires_human_approval is False
    assert "CRITICAL" in result.reason


def test_high_auto_approved(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    req = make_request(risk=RiskLevel.HIGH)
    result = dpm.request_permission(req, task_authorized=True)
    assert result.decision == PermissionDecision.ALLOW
    assert result.grant is not None
    assert result.requires_human_approval is False
    assert "HIGH" in result.reason


def test_hard_checks_still_block_high_and_critical(tmp_path):
    """Auto-approval must never bypass scope, always_denied, or profile checks."""
    from arcanum.permissions.permission_policy import PolicyConfig

    dpm, *_ = make_dpm(tmp_path, policy=PermissionPolicy(
        PolicyConfig(always_denied={"http_read"})
    ))
    for index, risk in enumerate((RiskLevel.HIGH, RiskLevel.CRITICAL)):
        result = dpm.request_permission(
            make_request(risk=risk, request_id=f"req_hard_{index}"),
            task_authorized=True,
            scope_verified=True,
        )
        assert result.decision == PermissionDecision.DENY
        assert result.grant is None


def test_low_auto_approved_and_grant_issued(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    req = make_request(risk=RiskLevel.LOW)
    result = dpm.request_permission(req, task_authorized=True)
    assert result.decision == PermissionDecision.ALLOW
    assert result.grant is not None
    assert result.grant.is_usable
    assert result.grant.agent_id == "recon_agent"
    assert result.grant.task_id == "task_1"
    assert result.grant.capability == "http_read"


def test_medium_auto_approved_by_default(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    req = make_request(risk=RiskLevel.MEDIUM)
    result = dpm.request_permission(req, task_authorized=True)
    assert result.decision == PermissionDecision.ALLOW
    assert result.grant is not None
    assert result.requires_human_approval is False


def test_medium_auto_approved_when_policy_lists_capability(tmp_path):
    policy = PermissionPolicy(PolicyConfig(medium_auto_approved_capabilities={"http_security_test"}))
    dpm, *_ = make_dpm(tmp_path, policy=policy)
    req = make_request(agent_id="web_security_agent", capability="http_security_test", risk=RiskLevel.MEDIUM)
    result = dpm.request_permission(req, task_authorized=True)
    assert result.decision == PermissionDecision.ALLOW


def test_grant_expiry(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    req = make_request(duration_seconds=1)
    result = dpm.request_permission(req, task_authorized=True)
    grant = result.grant
    assert grant is not None
    # Force expiry
    grant.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    dpm.store.save(grant)
    valid, reason, g = dpm.validate_grant(grant.grant_id, "recon_agent", "task_1", "http_read", "lab_app", "read")
    assert valid is False
    assert "expired" in reason.lower()


def test_grant_usage_limit(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    req = make_request()
    result = dpm.request_permission(req, task_authorized=True)
    grant = result.grant
    assert grant is not None
    valid, reason, _ = dpm.validate_grant(grant.grant_id, "recon_agent", "task_1", "http_read", "lab_app", "read")
    assert valid
    dpm.consume_grant(grant.grant_id)
    valid2, reason2, _ = dpm.validate_grant(grant.grant_id, "recon_agent", "task_1", "http_read", "lab_app", "read")
    assert valid2 is False
    assert "usage limit" in reason2.lower()


def test_cross_agent_grant_invalid(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    req = make_request()
    result = dpm.request_permission(req, task_authorized=True)
    grant = result.grant
    assert grant is not None
    valid, reason, _ = dpm.validate_grant(grant.grant_id, "web_security_agent", "task_1", "http_read", "lab_app", "read")
    assert valid is False
    assert "different agent" in reason.lower()


def test_cross_task_grant_invalid(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    req = make_request()
    result = dpm.request_permission(req, task_authorized=True)
    grant = result.grant
    assert grant is not None
    valid, reason, _ = dpm.validate_grant(grant.grant_id, "recon_agent", "task_2", "http_read", "lab_app", "read")
    assert valid is False
    assert "different task" in reason.lower()


def test_revoked_grant_invalid(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    req = make_request()
    result = dpm.request_permission(req, task_authorized=True)
    grant = result.grant
    assert grant is not None
    dpm.revoke_grant(grant.grant_id, "security_violation")
    valid, reason, _ = dpm.validate_grant(grant.grant_id, "recon_agent", "task_1", "http_read", "lab_app", "read")
    assert valid is False
    assert "revoked" in reason.lower()


def test_revoke_for_task(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    req1 = make_request(task_id="task_1")
    req2 = make_request(task_id="task_2", agent_id="web_security_agent", capability="http_security_test")
    r1 = dpm.request_permission(req1, task_authorized=True)
    r2 = dpm.request_permission(req2, task_authorized=True)
    assert r1.grant and r2.grant
    count = dpm.revoke_for_task("task_1")
    assert count >= 1
    valid, _, _ = dpm.validate_grant(r1.grant.grant_id, "recon_agent", "task_1", "http_read", "lab_app", "read")
    assert valid is False


def test_human_approval_creates_grant(tmp_path):
    """HITL is still testable via an explicit policy that requires MEDIUM approval."""
    dpm, *_ = make_dpm(tmp_path, policy=PermissionPolicy(
        PolicyConfig(allow_auto_medium=False)
    ))
    req = make_request(risk=RiskLevel.MEDIUM)
    result = dpm.request_permission(req, task_authorized=True)
    assert result.decision == PermissionDecision.REQUIRE_HUMAN_APPROVAL
    assert result.grant is None
    approved = dpm.approve_request(req, task_authorized=True)
    assert approved.decision == PermissionDecision.ALLOW
    assert approved.grant is not None
    assert approved.grant.is_usable


def test_escalate_logs_audit(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    req = make_request()
    result = dpm.escalate(req, task_authorized=True)
    assert result.decision == PermissionDecision.ALLOW
    records = dpm.audit.list_records(agent_id="recon_agent")
    types = [r["event_type"] for r in records]
    assert "escalation_request" in types


def test_audit_records_are_append_only(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    req = make_request()
    dpm.request_permission(req, task_authorized=True)
    records = dpm.audit.list_records()
    assert len(records) >= 1
    # No delete method exists
    assert not hasattr(dpm.audit, "delete")


def test_orchestrator_cannot_bypass_dpm(tmp_path):
    """The orchestrator has no grant method and no tool execution method."""
    from arcanum.core.orchestrator import Orchestrator
    from arcanum.core.task_manager import TaskManager
    from arcanum.memory.database import Database
    from arcanum.security.scope_manager import ScopeManager
    from arcanum.config.settings import Settings

    s = Settings(base_dir=tmp_path, db_path=tmp_path / "db.sqlite",
                 targets_dir=tmp_path / "T", allowed_operators=["op"])
    db = Database(s.db_path)
    sm = ScopeManager(db, s)
    tm = TaskManager(db, sm, s)
    orch = Orchestrator(tm)
    assert not hasattr(orch, "grant_permission")
    assert not hasattr(orch, "execute_tool")
    assert not hasattr(orch, "approve_grant")


def test_proxy_capabilities_registered_in_profiles():
    """Architecture 8.1: all four proxy tools must be requestable."""
    assert PROXY_CAPABILITIES <= DEFAULT_PROFILES["recon_agent"].max_capabilities | DEFAULT_PROFILES["web_security_agent"].max_capabilities
    assert {"caido_read", "caido_traffic_query"} <= DEFAULT_PROFILES["recon_agent"].max_capabilities
    assert {"burp_intercept", "burp_repeater"} <= DEFAULT_PROFILES["web_security_agent"].max_capabilities


def test_passive_recon_allows_caido_read(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    req = make_request(agent_id="recon_agent", capability="caido_read",
                       task_type="passive_recon")
    result = dpm.request_permission(req, task_authorized=True)
    assert result.decision == PermissionDecision.ALLOW


def test_passive_recon_denies_burp_for_wrong_task_type(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    req = make_request(agent_id="web_security_agent", capability="burp_intercept",
                       task_type="passive_recon", risk=RiskLevel.MEDIUM)
    result = dpm.request_permission(req, task_authorized=True)
    assert result.decision == PermissionDecision.DENY
    assert result.policy_ref == "proxy_tool_not_allowed_for_task_type"


def test_active_proxy_requires_human_approval(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    req = make_request(agent_id="web_security_agent", capability="burp_intercept",
                       task_type="active_web_test", risk=RiskLevel.MEDIUM)
    result = dpm.request_permission(req, task_authorized=True)
    assert result.decision == PermissionDecision.REQUIRE_HUMAN_APPROVAL
    assert result.requires_human_approval is True
    # Human approval is a valid step-up for active proxy work.
    approved = dpm.approve_request(req, task_authorized=True)
    assert approved.decision == PermissionDecision.ALLOW
    assert approved.grant is not None


def test_alternative_proxy_tool_after_denial_denied(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    first = make_request(agent_id="web_security_agent", capability="caido_read",
                         task_type="active_web_test", risk=RiskLevel.LOW,
                         request_id="r_denied")
    first_result = dpm.request_permission(first, task_authorized=True)
    assert first_result.decision == PermissionDecision.DENY

    alternative = make_request(agent_id="web_security_agent", capability="burp_intercept",
                               task_type="active_web_test", risk=RiskLevel.MEDIUM,
                               request_id="r_alternative")
    second_result = dpm.request_permission(alternative, task_authorized=True)
    assert second_result.decision == PermissionDecision.DENY
    assert second_result.policy_ref == "alternative_proxy_tool_after_denial"


def test_human_approval_cannot_override_always_denied(tmp_path):
    policy = PermissionPolicy(PolicyConfig(always_denied={"http_read"}))
    dpm, *_ = make_dpm(tmp_path, policy=policy)
    req = make_request(risk=RiskLevel.HIGH)
    result = dpm.approve_request(req, task_authorized=True)
    assert result.decision == PermissionDecision.DENY
    assert result.policy_ref == "always_denied"
    assert result.grant is None


def test_grant_constraints_are_populated(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    req = make_request(max_requests=3, allowed_methods=["GET"],
                       rate_limit_per_minute=2)
    result = dpm.request_permission(req, task_authorized=True)
    assert result.grant is not None
    assert result.grant.usage_limit == 3
    assert result.grant.constraints["allowed_methods"] == ["GET"]
    assert result.grant.constraints["rate_limit_per_minute"] == 2


def test_scope_verifier_receives_operation(tmp_path):
    seen = []
    store = PermissionStore(tmp_path / "grants.db")
    audit = PermissionAuditLog(tmp_path / "audit.db")
    dpm = DynamicPermissionManager(
        store=store, audit=audit,
        scope_verifier=lambda target, operation: seen.append((target, operation)) or True,
    )
    req = make_request(operation="fingerprint")
    dpm.request_permission(req, task_authorized=True)
    assert ("lab_app", "fingerprint") in seen


def test_task_type_flows_from_task_to_agent_message(tmp_path):
    from arcanum.agents.base_agent import BaseAgent
    from arcanum.config.settings import Settings
    from arcanum.core.orchestrator import Orchestrator
    from arcanum.core.task_manager import TaskManager
    from arcanum.memory.database import Database
    from arcanum.security.scope_manager import ScopeManager

    s = Settings(base_dir=tmp_path, db_path=tmp_path / "db.sqlite",
                 targets_dir=tmp_path / "T", allowed_operators=["op"])
    db = Database(s.db_path)
    orch = Orchestrator(TaskManager(db, ScopeManager(db, s), s))
    orch.register_agent(BaseAgent("recon_agent", "recon"))

    task = orch.receive_objective("Assess example.com", "op",
                                  task_type="passive_recon")
    assert task.task_type == "passive_recon"
    ack = orch.dispatch(task, "recon_agent")
    assert ack.task_type == "passive_recon"
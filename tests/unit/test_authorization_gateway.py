from datetime import datetime, timedelta, timezone

import pytest

from arcanum.permissions.authorization_gateway import AuthorizationGateway
from arcanum.permissions.permission_manager import DynamicPermissionManager, PermissionStore
from arcanum.permissions.permission_audit import PermissionAuditLog
from arcanum.permissions.permission_models import (
    DEFAULT_PROFILES,
    PermissionDecision,
    PermissionRequest,
    RiskLevel,
)
from arcanum.permissions.permission_policy import PermissionPolicy


def make_dpm(tmp_path, scope_ok=True):
    store = PermissionStore(tmp_path / "grants.db")
    audit = PermissionAuditLog(tmp_path / "audit.db")
    return DynamicPermissionManager(
        store=store, audit=audit, policy=PermissionPolicy(),
        profiles=DEFAULT_PROFILES, scope_verifier=lambda _: scope_ok,
    ), store, audit


def test_unregistered_tool_denied(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    gw = AuthorizationGateway(dpm)
    ok, result, reason = gw.execute(
        "http_read", "recon_agent", "task_1", "lab_app", "read",
        "grant_fake", params={}
    )
    assert ok is False
    assert "not registered" in reason


def test_execute_without_valid_grant_denied(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    gw = AuthorizationGateway(dpm)
    gw.register_tool("read_tool", "http_read", lambda p: "data", "read")
    ok, result, reason = gw.execute(
        "read_tool", "recon_agent", "task_1", "lab_app", "read",
        "grant_does_not_exist", params={}
    )
    assert ok is False
    assert "not found" in reason.lower()
    assert result is None


def test_execute_with_valid_grant_succeeds_and_consumes(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    gw = AuthorizationGateway(dpm)
    gw.register_tool("read_tool", "http_read", lambda p: "secret_data", "read")
    req = PermissionRequest(
        request_id="r1", agent_id="recon_agent", task_id="task_1",
        requested_capability="http_read", target="lab_app", operation="read",
        justification="test", duration_seconds=60, risk=RiskLevel.LOW,
    )
    decision = dpm.request_permission(req, task_authorized=True)
    assert decision.decision == PermissionDecision.ALLOW
    grant_id = decision.grant.grant_id
    ok, result, reason = gw.execute(
        "read_tool", "recon_agent", "task_1", "lab_app", "read",
        grant_id, params={}
    )
    assert ok is True
    assert result == "secret_data"
    # Second use should fail (usage limit 1)
    ok2, _, reason2 = gw.execute(
        "read_tool", "recon_agent", "task_1", "lab_app", "read",
        grant_id, params={}
    )
    assert ok2 is False
    assert "usage limit" in reason2.lower()


def test_wrong_tool_capability_denied(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    gw = AuthorizationGateway(dpm)
    gw.register_tool("write_tool", "finding_write", lambda p: "ok", "write")
    # Grant is for http_read, not finding_write
    req = PermissionRequest(
        request_id="r1", agent_id="recon_agent", task_id="task_1",
        requested_capability="http_read", target="lab_app", operation="read",
        justification="test", duration_seconds=60, risk=RiskLevel.LOW,
    )
    decision = dpm.request_permission(req, task_authorized=True)
    grant_id = decision.grant.grant_id
    ok, result, reason = gw.execute(
        "write_tool", "recon_agent", "task_1", "lab_app", "read",
        grant_id, params={}
    )
    assert ok is False
    assert "does not cover" in reason.lower() or "not cover" in reason.lower()


def test_revoked_grant_denied(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    gw = AuthorizationGateway(dpm)
    gw.register_tool("read_tool", "http_read", lambda p: "data", "read")
    req = PermissionRequest(
        request_id="r1", agent_id="recon_agent", task_id="task_1",
        requested_capability="http_read", target="lab_app", operation="read",
        justification="test", duration_seconds=60, risk=RiskLevel.LOW,
    )
    decision = dpm.request_permission(req, task_authorized=True)
    grant_id = decision.grant.grant_id
    dpm.revoke_grant(grant_id, "security_violation")
    ok, result, reason = gw.execute(
        "read_tool", "recon_agent", "task_1", "lab_app", "read",
        grant_id, params={}
    )
    assert ok is False
    assert "revoked" in reason.lower()


def test_tool_handler_exception_revokes_grant(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    gw = AuthorizationGateway(dpm)
    def bad_handler(p):
        raise RuntimeError("boom")
    gw.register_tool("bad_tool", "http_read", bad_handler, "bad")
    req = PermissionRequest(
        request_id="r1", agent_id="recon_agent", task_id="task_1",
        requested_capability="http_read", target="lab_app", operation="read",
        justification="test", duration_seconds=60, risk=RiskLevel.LOW,
    )
    decision = dpm.request_permission(req, task_authorized=True)
    grant_id = decision.grant.grant_id
    ok, result, reason = gw.execute(
        "bad_tool", "recon_agent", "task_1", "lab_app", "read",
        grant_id, params={}
    )
    assert ok is False
    assert "failed" in reason.lower()
    # Grant should be revoked after failure
    valid, _, _ = dpm.validate_grant(grant_id, "recon_agent", "task_1", "http_read", "lab_app", "read")
    assert valid is False


def test_list_tools(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    gw = AuthorizationGateway(dpm)
    gw.register_tool("read_tool", "http_read", lambda p: "data", "read")
    gw.register_tool("write_tool", "finding_write", lambda p: "ok", "write")
    tools = gw.list_tools()
    assert len(tools) == 2
    names = {t["name"] for t in tools}
    assert names == {"read_tool", "write_tool"}
    assert gw.has_tool("read_tool")
    assert not gw.has_tool("nonexistent")


def test_gateway_audits_every_authorization_attempt(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    gw = AuthorizationGateway(dpm)
    gw.register_tool("read_tool", "http_read", lambda p: "data", "read")
    req = PermissionRequest(
        request_id="r1", agent_id="recon_agent", task_id="task_1",
        requested_capability="http_read", target="lab_app", operation="read",
        justification="test",
    )
    grant_id = dpm.request_permission(req).grant.grant_id
    gw.execute("read_tool", "recon_agent", "task_1", "lab_app", "read", grant_id)
    # A second attempt uses the now-consumed grant and must also be audited.
    gw.execute("read_tool", "recon_agent", "task_1", "lab_app", "read", grant_id)
    events = [r["event_type"] for r in dpm.audit.list_records()]
    assert events.count("gateway_authorization_attempt") == 2
    assert "tool_execution" in events


def test_live_scope_change_revokes_grant(tmp_path):
    scope = {"ok": True}
    store = PermissionStore(tmp_path / "grants.db")
    audit = PermissionAuditLog(tmp_path / "audit.db")
    dpm = DynamicPermissionManager(
        store=store, audit=audit, policy=PermissionPolicy(),
        profiles=DEFAULT_PROFILES,
        scope_verifier=lambda target, operation="": scope["ok"],
    )
    gw = AuthorizationGateway(dpm)
    gw.register_tool("read_tool", "http_read", lambda p: "data", "read")
    req = PermissionRequest(
        request_id="r1", agent_id="recon_agent", task_id="task_1",
        requested_capability="http_read", target="lab_app", operation="read",
        justification="test",
    )
    grant_id = dpm.request_permission(req).grant.grant_id
    scope["ok"] = False  # operator narrows scope after grant issuance
    ok, _, reason = gw.execute("read_tool", "recon_agent", "task_1",
                               "lab_app", "read", grant_id)
    assert ok is False
    assert "scope" in reason.lower()
    valid, _, _ = dpm.validate_grant(grant_id, "recon_agent", "task_1",
                                     "http_read", "lab_app", "read")
    assert valid is False


def test_allowed_methods_constraint_enforced(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    gw = AuthorizationGateway(dpm)
    gw.register_tool("http_tool", "http_read", lambda p: "data", "http")
    req = PermissionRequest(
        request_id="r1", agent_id="recon_agent", task_id="task_1",
        requested_capability="http_read", target="lab_app", operation="read",
        justification="test", allowed_methods=["GET"],
    )
    grant_id = dpm.request_permission(req).grant.grant_id
    ok, _, reason = gw.execute("http_tool", "recon_agent", "task_1",
                               "lab_app", "read", grant_id,
                               params={"method": "POST"})
    assert ok is False
    assert "not allowed" in reason.lower()


def test_max_requests_constraint_enforced(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    gw = AuthorizationGateway(dpm)
    gw.register_tool("read_tool", "http_read", lambda p: "data", "read")
    req = PermissionRequest(
        request_id="r1", agent_id="recon_agent", task_id="task_1",
        requested_capability="http_read", target="lab_app", operation="read",
        justification="test", max_requests=2,
    )
    grant_id = dpm.request_permission(req).grant.grant_id
    assert gw.execute("read_tool", "recon_agent", "task_1", "lab_app", "read", grant_id)[0]
    assert gw.execute("read_tool", "recon_agent", "task_1", "lab_app", "read", grant_id)[0]
    ok, _, reason = gw.execute("read_tool", "recon_agent", "task_1",
                               "lab_app", "read", grant_id)
    assert ok is False
    assert "usage limit" in reason.lower()


def test_rate_limit_constraint_enforced(tmp_path):
    dpm, *_ = make_dpm(tmp_path)
    gw = AuthorizationGateway(dpm)
    gw.register_tool("read_tool", "http_read", lambda p: "data", "read")
    req = PermissionRequest(
        request_id="r1", agent_id="recon_agent", task_id="task_1",
        requested_capability="http_read", target="lab_app", operation="read",
        justification="test", max_requests=3, rate_limit_per_minute=1,
    )
    grant_id = dpm.request_permission(req).grant.grant_id
    assert gw.execute("read_tool", "recon_agent", "task_1", "lab_app", "read", grant_id)[0]
    ok, _, reason = gw.execute("read_tool", "recon_agent", "task_1",
                               "lab_app", "read", grant_id)
    assert ok is False
    assert "rate limit" in reason.lower()
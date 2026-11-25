from pathlib import Path

import pytest

from arcanum.agents import SkillRegistry
from arcanum.config.settings import Settings
from arcanum.permissions.approval_queue import ApprovalQueue
from arcanum.permissions.permission_models import PermissionRequest, RiskLevel
from arcanum import runtime


def settings_for(tmp_path):
    return Settings(
        base_dir=tmp_path,
        db_path=tmp_path / "db.sqlite",
        targets_dir=tmp_path / "Targets",
        approval_db=tmp_path / "approvals.sqlite",
        allowed_operators=["operator1"],
        require_medium_approval=True,
    )


def skill_root(tmp_path):
    root = tmp_path / "skills"
    d = root / "hunt-xss"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text("# hunt-xss", encoding="utf-8")
    return root


def test_run_phase_uses_deterministic_path(tmp_path, monkeypatch):
    s = settings_for(tmp_path)
    monkeypatch.setattr(runtime.bridge, "SkillRegistry", lambda: SkillRegistry([skill_root(tmp_path)]))
    monkeypatch.setattr(runtime.bridge, "install_caido_adapter", lambda registry, adapter=None: adapter)
    monkeypatch.setattr(runtime.bridge, "install_burp_adapter", lambda registry, adapter=None: adapter)
    out = runtime.run_phase(
        objective="Assess example.com",
        skill_id="hunt-xss",
        capability="caido_read",
        operation="read",
        target="example.com",
        operator="operator1",
        task_type="passive_recon",
        params={"host": "lab.example.test"},
        settings=s,
    )
    assert out["decision"] == "ALLOW"
    assert out["allowed"] is True
    assert out["result"]["untrusted_data"]["source"] == "mock_caido_store"


def test_pending_approval_is_shared_with_bridge(tmp_path):
    s = settings_for(tmp_path)
    queue = ApprovalQueue(s.approval_db)
    dpm = runtime.bridge._stack(s)[2]
    req = PermissionRequest(
        request_id="req_bridge", agent_id="recon_agent", task_id="task_1",
        requested_capability="http_read", target="example.com", operation="read",
        justification="test", risk=RiskLevel.MEDIUM,
    )
    dpm.request_permission(req, task_authorized=True, scope_verified=True)
    dpm.close()
    queue.close()

    pending = runtime.list_pending(s)
    assert len(pending) == 1
    assert "`approval_" in runtime.format_pending(s)


def test_approval_auto_retries_original_phase(tmp_path, monkeypatch):
    s = settings_for(tmp_path)
    monkeypatch.setattr(runtime.bridge, "SkillRegistry", lambda: SkillRegistry([skill_root(tmp_path)]))
    monkeypatch.setattr(runtime.bridge, "install_caido_adapter", lambda registry, adapter=None: adapter)
    monkeypatch.setattr(runtime.bridge, "install_burp_adapter", lambda registry, adapter=None: adapter)

    first = runtime.run_phase(
        objective="Assess example.com",
        skill_id="hunt-xss",
        capability="http_security_test",
        operation="test",
        target="example.com",
        operator="operator1",
        task_type="active_web_test",
        risk="MEDIUM",
        settings=s,
    )
    assert first["decision"] == "REQUIRE_HUMAN_APPROVAL"
    pending = runtime.list_pending(s)
    assert len(pending) == 1

    approved = runtime.approve_pending(pending[0]["approval_id"], "operator1", s)
    assert approved["decision"] == "ALLOW"
    assert approved["retried"] is not None
    assert approved["retried"]["decision"] == "ALLOW"


def test_bridge_approve_creates_grant(tmp_path):
    s = settings_for(tmp_path)
    _, queue, dpm, _, _, _, tm = runtime.bridge._stack(s)
    task = tm.create_task("Assess example.com", "operator1")
    req = PermissionRequest(
        request_id="req_bridge2", agent_id="recon_agent", task_id=task.id,
        requested_capability="http_read", target="example.com", operation="read",
        justification="test", risk=RiskLevel.MEDIUM,
    )
    dpm.request_permission(req, task_authorized=True, scope_verified=True)
    approval_id = queue.list_pending()[0]["approval_id"]
    dpm.close(); queue.close()

    result = runtime.approve_pending(approval_id, "operator1", s)
    assert result["decision"] == "ALLOW"
    assert result["grant_id"].startswith("grant_")

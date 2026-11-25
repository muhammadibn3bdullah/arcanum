from pathlib import Path

from arcanum.agents import SkillRegistry
from arcanum.config.settings import Settings
from arcanum.routing import PhaseRouter
from arcanum import runtime


def skill_root(tmp_path: Path, names: list[str]) -> Path:
    root = tmp_path / "skills"
    for name in names:
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"# {name}", encoding="utf-8")
    return root


def settings_for(tmp_path):
    return Settings(
        base_dir=tmp_path,
        db_path=tmp_path / "db.sqlite",
        targets_dir=tmp_path / "Targets",
        approval_db=tmp_path / "approvals.sqlite",
        allowed_operators=["op"],
    )


def test_router_infers_task_types():
    router = PhaseRouter(SkillRegistry([]))
    assert router.infer_task_type("passive fingerprint example.com") == "passive_recon"
    assert router.infer_task_type("hunt XSS on example.com") == "active_web_test"
    assert router.infer_task_type("validate this evidence") == "validation"
    assert router.infer_task_type("write a report") == "reporting"
    assert router.infer_task_type("hello there") == "general"


def test_router_selects_passive_plan(tmp_path):
    root = skill_root(tmp_path, ["api-fingerprint-caido", "hunt-xss"])
    router = PhaseRouter(SkillRegistry([root]))
    plan = router.select("passive fingerprint example.com", "example.com")
    assert plan is not None
    assert plan.skill_id == "api-fingerprint-caido"
    assert plan.capability == "caido_read"
    assert plan.task_type == "passive_recon"
    assert plan.params["host"] == "example.com"


def test_router_selects_active_plan(tmp_path):
    root = skill_root(tmp_path, ["hunt-xss"])
    router = PhaseRouter(SkillRegistry([root]))
    plan = router.select("hunt XSS on example.com", "example.com")
    assert plan is not None
    assert plan.skill_id == "hunt-xss"
    assert plan.capability == "http_security_test"
    assert plan.risk == "MEDIUM"


def test_router_returns_none_without_installed_skill():
    router = PhaseRouter(SkillRegistry([]))
    assert router.select("passive recon example.com", "example.com") is None


def test_handle_chat_routes_targeted_objective(tmp_path, monkeypatch):
    s = settings_for(tmp_path)
    root = skill_root(tmp_path, ["api-fingerprint-caido"])
    monkeypatch.setattr(
        runtime.bridge, "PhaseRouter",
        lambda: PhaseRouter(SkillRegistry([root])),
    )
    monkeypatch.setattr(runtime.bridge, "SkillRegistry", lambda: SkillRegistry([root]))
    monkeypatch.setattr(runtime.bridge, "install_caido_adapter", lambda registry, adapter=None: adapter)
    monkeypatch.setattr(runtime.bridge, "install_burp_adapter", lambda registry, adapter=None: adapter)

    out = runtime.handle_chat("passive fingerprint example.com", "op", s)
    assert out is not None
    assert out["decision"] == "ALLOW"
    assert out["allowed"] is True
    assert out["result"]["untrusted_data"]["source"] == "mock_caido_store"


def test_handle_chat_ignores_non_target_chat(tmp_path):
    out = runtime.handle_chat("hello there", "op", settings_for(tmp_path))
    assert out is None

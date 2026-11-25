import importlib.util
import json
from pathlib import Path

import pytest

from arcanum.runtime import maestro
from arcanum.runtime.tab_registry import TabRegistry


ROOT = Path(__file__).resolve().parents[2]


def test_get_or_create_agent_tab_reuses_valid_tab(tmp_path, monkeypatch):
    reg = TabRegistry(tmp_path / "tabs.sqlite")
    reg.put_specialist("wf", "target_a", "recon-agent", "tab_a")
    calls = []

    def fake_resolve_tab(tab_id):
        return {"tabId": tab_id, "agentName": "recon-agent"}

    monkeypatch.setattr(maestro, "resolve_tab", fake_resolve_tab)
    monkeypatch.setattr(maestro, "get_transcript", lambda t: ["m1", "m2"])

    def fake_json(args, timeout=120):
        calls.append(list(args))
        return {"success": True, "tabId": "tab_a"}

    monkeypatch.setattr(maestro, "_json", fake_json)

    tab, created, cursor, raw = maestro.get_or_create_agent_tab(
        "wf", "target_a", "recon-agent", "task",
        registry=reg, task_id="task_1", execution_id="exec_1",
    )
    assert tab == "tab_a"
    assert created is False
    assert cursor == 2
    assert calls[0][:5] == ["dispatch", "--tab", "tab_a", "--queue", "recon-agent"]
    assert reg.events_for_workflow("wf")[-1]["action"] == "reuse"
    reg.close()


def test_get_or_create_agent_tab_creates_once_after_stale(tmp_path, monkeypatch):
    reg = TabRegistry(tmp_path / "tabs.sqlite")
    reg.put_specialist("wf", "target_a", "recon-agent", "old_tab")

    def fake_resolve_tab(tab_id):
        if tab_id == "old_tab":
            raise KeyError(tab_id)
        return {"tabId": tab_id, "agentName": "recon-agent"}

    def fake_json(args, timeout=120):
        return {"success": True, "tabId": "new_tab"}

    monkeypatch.setattr(maestro, "resolve_tab", fake_resolve_tab)
    monkeypatch.setattr(maestro, "get_transcript", lambda t: [])
    monkeypatch.setattr(maestro, "_json", fake_json)

    tab, created, cursor, raw = maestro.get_or_create_agent_tab(
        "wf", "target_a", "recon-agent", "task", registry=reg,
    )
    assert tab == "new_tab"
    assert created is True
    assert cursor == 0
    assert reg.get_specialist("wf", "target_a", "recon-agent")["tab_id"] == "new_tab"
    actions = [e["action"] for e in reg.events_for_workflow("wf")]
    assert "stale_replaced" in actions and "create" in actions
    reg.close()


def test_unexpected_new_tab_for_registered_mapping_is_refused(tmp_path, monkeypatch):
    reg = TabRegistry(tmp_path / "tabs.sqlite")
    reg.put_specialist("wf", "target_a", "recon-agent", "tab_a")
    monkeypatch.setattr(maestro, "resolve_tab", lambda t: {
        "tabId": t, "agentName": "recon-agent",
    })

    with pytest.raises(RuntimeError, match="Refusing unregistered tab"):
        maestro.assert_registered_tab("wf", "target_a", "recon-agent", "other_tab", reg)
    assert reg.events_for_workflow("wf")[-1]["action"] == "refuse_unregistered_tab"
    reg.close()


def _load(script_name):
    spec = importlib.util.spec_from_file_location(script_name, ROOT / "scripts" / script_name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lead_prompt_and_permissions_do_not_allow_raw_new_tab():
    module = _load("wire_maestro_agents.py")
    lead = module.SYSTEMS["hackbot-lead"]
    assert "arcanum-tab-dispatch" in lead
    assert "maestro-cli dispatch --new-tab" not in lead
    assert "maestro-cli dispatch <" not in lead
    for name in ("recon-agent", "hunt-agent", "validate-agent", "report-agent"):
        assert "Do NOT create new Maestro tabs" in module.SYSTEMS[name]


def test_hardening_denies_raw_dispatch_and_allows_wrapper(tmp_path, monkeypatch):
    module = _load("harden_opencode_permissions.py")
    config = tmp_path / "opencode.json"
    config.write_text(json.dumps({"agents": {"hackbot-lead": {}}}), encoding="utf-8")
    monkeypatch.setattr(module, "CONFIG", config)
    monkeypatch.setattr(module, "shutil", __import__("shutil"))
    module.main()
    rules = json.loads(config.read_text())["agents"]["hackbot-lead"]["permissions"]
    assert any(
        r["action"] == "shell" and "maestro-cli dispatch" in r["resource"]
        and r["effect"] == "deny" for r in rules
    )
    assert any(
        r["action"] == "shell" and "arcanum-tab-dispatch" in r["resource"]
        and r["effect"] == "allow" for r in rules
    )


def test_arcanum_playbook_contains_no_raw_new_tab_dispatch():
    playbook = (
        Path(__file__).resolve().parents[2]
        / "workflows" / "maestro" / "playbooks" / "ARCANUM-01.md"
    )
    text = playbook.read_text(encoding="utf-8")
    assert "arcanum-tab-dispatch" in text
    assert "maestro-cli dispatch --new-tab" not in text
    assert "maestro-cli dispatch <" not in text

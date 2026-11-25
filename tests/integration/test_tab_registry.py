import sqlite3

from arcanum.runtime.tab_registry import TabRegistry, SCHEMA_VERSION


def test_uniform_key_for_all_five_agents(tmp_path):
    reg = TabRegistry(tmp_path / "tabs.sqlite")
    agents = [
        "hackbot-lead", "recon-agent", "hunt-agent", "validate-agent", "report-agent",
    ]
    for agent in agents:
        reg.put("wf", "target_a", agent, f"{agent}_a")
        reg.put("wf", "target_b", agent, f"{agent}_b")

    assert reg.count_for_workflow("wf") == 10
    assert reg.tab_counts_by_agent("wf") == {agent: 2 for agent in agents}
    for agent in agents:
        assert reg.get("wf", "target_a", agent)["tab_id"] == f"{agent}_a"
        assert reg.get("wf", "target_b", agent)["tab_id"] == f"{agent}_b"
        assert f"{agent}_a" != f"{agent}_b"
    assert reg.get_lead("wf", "target_a")["tab_id"] == "hackbot-lead_a"
    assert reg.get_lead("wf", "target_b")["tab_id"] == "hackbot-lead_b"
    reg.close()


def test_active_counts_and_stale_marking(tmp_path):
    reg = TabRegistry(tmp_path / "tabs.sqlite")
    reg.put("wf", "a", "hackbot-lead", "lead_a")
    reg.put("wf", "a", "recon-agent", "recon_a")
    reg.put("wf", "b", "recon-agent", "recon_b")
    assert reg.count_for_workflow("wf", "active") == 3

    reg.mark_stale("wf", "a", "recon-agent")
    assert reg.get("wf", "a", "recon-agent")["status"] == "stale"
    assert reg.count_for_workflow("wf", "stale") == 1
    assert len(reg.active_for_workflow("wf")) == 2
    reg.close()


def test_validate_checks_agent_and_existence(tmp_path, monkeypatch):
    from arcanum.runtime import maestro

    reg = TabRegistry(tmp_path / "tabs.sqlite")
    reg.put("wf", "a", "recon-agent", "tab_a")

    monkeypatch.setattr(
        maestro, "resolve_tab",
        lambda tab_id: {"tabId": tab_id, "agentName": "recon-agent"},
    )
    mapping = reg.get("wf", "a", "recon-agent")
    assert reg.validate(mapping, "recon-agent")
    assert not reg.validate(mapping, "hunt-agent")

    monkeypatch.setattr(
        maestro, "resolve_tab",
        lambda tab_id: (_ for _ in ()).throw(KeyError(tab_id)),
    )
    assert not reg.validate(mapping, "recon-agent")
    reg.close()


def _create_v1_registry(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE maestro_tabs (
            scope TEXT NOT NULL, workflow_id TEXT NOT NULL, target_id TEXT NOT NULL,
            agent_id TEXT NOT NULL, tab_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
            last_task_id TEXT NOT NULL DEFAULT '', last_execution_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(scope, workflow_id, target_id, agent_id))"""
    )
    conn.execute(
        "INSERT INTO maestro_tabs(scope,workflow_id,target_id,agent_id,tab_id) "
        "VALUES ('specialist','wf_old','a.com','recon-agent','old_recon_a')"
    )
    conn.execute(
        "INSERT INTO maestro_tabs(scope,workflow_id,target_id,agent_id,tab_id) "
        "VALUES ('specialist','wf_old','b.com','recon-agent','old_recon_b')"
    )
    conn.execute(
        "INSERT INTO maestro_tabs(scope,workflow_id,target_id,agent_id,tab_id) "
        "VALUES ('lead','wf_old','','hackbot-lead','old_global_lead')"
    )
    conn.execute(
        "INSERT INTO maestro_tabs(scope,workflow_id,target_id,agent_id,tab_id) "
        "VALUES ('specialist','wf_single','solo.com','hunt-agent','old_hunt_solo')"
    )
    conn.execute(
        "INSERT INTO maestro_tabs(scope,workflow_id,target_id,agent_id,tab_id) "
        "VALUES ('lead','wf_single','','hackbot-lead','old_single_lead')"
    )
    conn.commit()
    conn.close()


def test_v1_migration_preserves_specialists_and_scopes_lead(tmp_path):
    path = tmp_path / "tabs.sqlite"
    _create_v1_registry(path)

    reg = TabRegistry(path)
    # Specialist mappings preserved.
    assert reg.get("wf_old", "a.com", "recon-agent")["tab_id"] == "old_recon_a"
    assert reg.get("wf_old", "b.com", "recon-agent")["tab_id"] == "old_recon_b"
    # Global Lead NOT assigned to either target (two targets in workflow).
    assert reg.get("wf_old", "a.com", "hackbot-lead") is None
    assert reg.get("wf_old", "b.com", "hackbot-lead") is None
    # Sole-target workflow keeps its Lead tab.
    assert reg.get("wf_single", "solo.com", "hackbot-lead")["tab_id"] == "old_single_lead"

    events = reg.events_for_workflow("wf_old")
    assert any(e["action"] == "lead_migration" and "archived" in e["reason"] for e in events)
    assert reg.conn.execute(
        "SELECT COUNT(*) AS n FROM maestro_tabs_legacy_v1"
    ).fetchone()["n"] == 5
    reg.close()

    # Idempotent across restarts.
    reg2 = TabRegistry(path)
    assert reg2.count_for_workflow("wf_old") == 2
    reg2.close()

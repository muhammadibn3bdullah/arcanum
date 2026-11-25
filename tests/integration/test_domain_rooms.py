import sqlite3

import pytest

from arcanum.config.settings import Settings
from arcanum.core.models import ScopeRef
from arcanum.core.rooms import AGENT_IDS, RoomMessageType
from arcanum.memory.database import Database
from arcanum.runtime import rooms


def scoped_db(tmp_path, domain="ses_ded2.local"):
    db = Database(tmp_path / "arcanum.db")
    db.save_scope(ScopeRef(
        scope_ref="scope_test", domain=domain, operator_id="local_operator",
        objective="authorized test target",
    ))
    return db


def test_one_room_per_target_and_five_agent_views(tmp_path):
    db = scoped_db(tmp_path)
    a = rooms.get_or_create_domain_room("wf", "ses_ded2.local", db=db)
    b = rooms.get_or_create_domain_room("wf", "ses_ded2.local", db=db)
    assert a.room_id == b.room_id
    assert db.conn.execute(
        "SELECT COUNT(*) AS n FROM domain_rooms WHERE workflow_id='wf'"
    ).fetchone()["n"] == 1
    assert len(db.room_views(a.room_id)) == 5
    for agent in AGENT_IDS:
        view = rooms.get_room_for_agent(agent, "wf", "ses_ded2.local", Settings(db_path=tmp_path / "arcanum.db"))
        assert view.room_id == a.room_id
    db.close()


def test_second_target_creates_second_room(tmp_path):
    db = scoped_db(tmp_path)
    db.save_scope(ScopeRef(
        scope_ref="scope_test2", domain="domain-b.com", operator_id="local_operator",
        objective="authorized test target",
    ))
    a = rooms.get_or_create_domain_room("wf", "ses_ded2.local", db=db)
    b = rooms.get_or_create_domain_room("wf", "domain-b.com", db=db)
    assert a.room_id != b.room_id
    assert len(db.list_rooms("wf")) == 2
    db.close()


def test_unauthorized_target_is_rejected(tmp_path):
    db = scoped_db(tmp_path)
    with pytest.raises(PermissionError):
        rooms.get_or_create_domain_room("wf", "unauthorized.example", db=db)
    db.close()


def test_message_published_once_and_views_read_same_history(tmp_path):
    db = scoped_db(tmp_path)
    room = rooms.get_or_create_domain_room("wf", "ses_ded2.local", db=db)
    settings = Settings(db_path=tmp_path / "arcanum.db")
    kwargs = dict(
        workflow_id="wf", target_id="ses_ded2.local",
        sender_agent_id="recon-agent", message_type=RoomMessageType.STRUCTURED_RESULT,
        content="result", task_id="task", execution_id="exec",
        idempotency_key="result:exec",
    )
    first = rooms.publish_message(settings=settings, **kwargs)
    second = rooms.publish_message(settings=settings, **kwargs)
    assert first.message_id == second.message_id
    messages = rooms.room_messages(room.room_id, settings=settings)
    assert len(messages) == 1
    assert messages[0]["sender_agent_id"] == "recon-agent"
    assert messages[0]["message_type"] == "STRUCTURED_RESULT"
    db.close()


def test_provider_quota_is_paused_and_posted_once(tmp_path, monkeypatch):
    from arcanum.core.task_brief import TaskBrief
    from arcanum.runtime import maestro, run_stage

    db = scoped_db(tmp_path)
    room = rooms.get_or_create_domain_room("wf", "ses_ded2.local", db=db)
    db.close()
    settings = Settings(db_path=tmp_path / "arcanum.db")

    def fail(*args, **kwargs):
        raise RuntimeError("provider usage limit reached for recon-agent")

    monkeypatch.setattr(maestro, "dispatch", fail)
    brief = TaskBrief(
        workflow_id="wf", target_id="ses_ded2.local", task_id="task_q",
        execution_id="exec_q", assigned_agent="recon-agent",
        objective="quota test", authorized_target="ses_ded2.local",
        scope_ref="scope_test", expected_activities=["parse task brief"],
        expected_deliverables=["result"], required_evidence=["structured result"],
    )
    result = run_stage(brief, settings=settings)
    assert result["decision"] == "BLOCKED"
    assert result["state"] == "WAITING_FOR_USER"
    messages = rooms.room_messages(room.room_id, settings=settings)
    assert len(messages) == 1
    assert messages[0]["message_type"] == "PROVIDER_QUOTA"
    assert messages[0]["sender_agent_id"] == "recon-agent"
    # Repeated failure does not create a second room or duplicate message.
    run_stage(brief, settings=settings)
    db2 = Database(settings.db_path)
    assert db2.conn.execute(
        "SELECT COUNT(*) AS n FROM domain_rooms WHERE workflow_id='wf'"
    ).fetchone()["n"] == 1
    assert len(rooms.room_messages(room.room_id, settings=settings)) == 1
    db2.close()


def test_sender_identity_is_not_merged(tmp_path):
    db = scoped_db(tmp_path)
    room = rooms.get_or_create_domain_room("wf", "ses_ded2.local", db=db)
    settings = Settings(db_path=tmp_path / "arcanum.db")
    rooms.publish_message(
        "wf", "ses_ded2.local", "hackbot-lead", RoomMessageType.TASK_ASSIGNMENT,
        "assign", idempotency_key="a", settings=settings,
    )
    rooms.publish_message(
        "wf", "ses_ded2.local", "hunt-agent", RoomMessageType.STRUCTURED_RESULT,
        "result", idempotency_key="b", settings=settings,
    )
    senders = [
        m["sender_agent_id"]
        for m in rooms.room_messages(room.room_id, settings=settings)
    ]
    assert senders == ["hackbot-lead", "hunt-agent"]
    db.close()

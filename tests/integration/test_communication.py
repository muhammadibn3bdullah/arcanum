import json

from arcanum.core.execution import AcceptanceCriteria
from arcanum.core.lifecycle import LifecycleState, MessageType
from arcanum.core.task_brief import TaskBrief
from arcanum.memory.database import Database
from arcanum.runtime import communication


def brief():
    return TaskBrief(
        workflow_id="wf_1", target_id="target_1", task_id="task_1",
        execution_id="exec_1", assigned_agent="recon-agent",
        objective="Map the authorized web surface",
        authorized_target="example.com", scope_ref="scope_1",
        available_capabilities=["caido_read"],
        expected_activities=["inspect traffic"],
        expected_deliverables=["structured result"],
        acceptance_criteria=AcceptanceCriteria(expected_agent_id="recon-agent"),
        required_evidence=["structured result"],
    )


def test_detect_clarification_from_structured_message():
    data = {
        "task_id": "task_1", "execution_id": "exec_1",
        "message_type": "CLARIFICATION_REQUEST",
        "question": "Which domain should I examine?",
    }
    transcript = [
        {"role": "user", "content": "TASK_ID=task_1"},
        {"role": "assistant", "content": "```json\n" + json.dumps(data) + "\n```"},
    ]
    msg_type, parsed = communication.detect_message(transcript, "task_1", "exec_1")
    assert msg_type == MessageType.CLARIFICATION_REQUEST
    assert parsed["question"] == "Which domain should I examine?"


def test_existing_information_clarification_does_not_wait_for_user():
    response, state = communication.resolve_clarification(
        brief(), "Which target and scope should I use?"
    )
    assert state == LifecycleState.RUNNING
    assert "example.com" in response
    assert "scope_1" in response
    assert "Do not ask the user" in response


def test_permission_question_waits_for_authorization():
    response, state = communication.resolve_clarification(
        brief(), "Can I get a shell grant?"
    )
    assert state == LifecycleState.WAITING_FOR_AUTHORIZATION
    assert "DPM-issued grant_id" in response


def test_unknown_question_waits_for_user():
    response, state = communication.resolve_clarification(
        brief(), "What is the customer's preferred reporting format?"
    )
    assert state == LifecycleState.WAITING_FOR_USER
    assert "user decision" in response


def test_followup_is_persisted_to_same_session(tmp_path, monkeypatch):
    sent = {}

    def fake_send(tab_id, message, **kwargs):
        sent["tab_id"] = tab_id
        sent["message"] = message
        sent["kwargs"] = kwargs
        return {"success": True}

    monkeypatch.setattr(communication.maestro, "send_followup", fake_send)
    db = Database(tmp_path / "db.sqlite")
    result = communication.respond_to_specialist(
        brief(), "Which domain should I inspect?", "tab_1", db
    )
    messages = db.messages_for_execution("exec_1")
    assert result["tab_id"] == "tab_1"
    assert sent["tab_id"] == "tab_1"
    assert sent["kwargs"]["agent"] == "recon-agent"
    assert sent["kwargs"]["target_id"] == "target_1"
    assert "example.com" in sent["message"]
    assert messages[-1]["message_type"] == "CLARIFICATION_RESPONSE"
    assert messages[-1]["sender"] == "hackbot-lead"
    db.close()

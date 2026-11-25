from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from arcanum.core.models import (
    AgentMessage,
    ScopeRef,
    Task,
    TaskStatus,
    ToolOperation,
)


def test_agent_message_roundtrip_and_no_extra_fields():
    msg = AgentMessage(
        task_id="task_1",
        from_agent="orchestrator",
        to_agent="recon_agent",
        objective="Passive recon",
        scope_refs=["scope_1"],
    )
    dumped = msg.model_dump()
    rebuilt = AgentMessage.model_validate(dumped)
    assert rebuilt.task_id == "task_1"
    assert "authorization" not in dumped
    with pytest.raises(ValidationError):
        AgentMessage(
            task_id="t",
            from_agent="a",
            to_agent="b",
            objective="x",
            authorization="granted",  # type: ignore[arg-type]
        )


def test_task_status_enum():
    assert TaskStatus.CREATED.value == "created"
    task = Task(id="t1", objective="obj", status="created")  # type: ignore[arg-type]
    assert task.status is TaskStatus.CREATED


def test_scope_ref_defaults_passive_first():
    scope = ScopeRef(
        scope_ref="s1",
        domain="example.com",
        operator_id="local_operator",
        objective="Assess example.com",
    )
    assert scope.active_testing_authorized is False
    assert "passive_recon" in scope.allowed_operations
    assert isinstance(scope.declared_at, datetime)


def test_tool_operation_is_request_not_grant():
    op = ToolOperation(
        request_id="r1",
        agent_id="web_agent",
        task_id="t1",
        capability="http_security_test",
        target="lab_app",
        operation="test_input_validation",
        justification="validate hypothesis",
    )
    assert op.grant_id is None
    assert op.risk == "LOW"

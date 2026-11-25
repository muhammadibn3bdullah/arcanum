import json

from arcanum.core.execution import (
    AcceptanceCriteria,
    DispatchAcknowledgment,
    ExecutionReport,
    ExecutionState,
    VerificationDecision,
)
from arcanum.core.verification import CompletionVerifier


def make_ack():
    return DispatchAcknowledgment(
        task_id="task_1", execution_id="exec_1", workflow_id="wf_1",
        target_id="target_1", agent_id="recon-agent", tab_id="tab_1",
    )


def result_json(**overrides):
    data = {
        "task_id": "task_1",
        "execution_id": "exec_1",
        "status": "completed",
        "actions_performed": ["maestro_dispatch", "opencode_assistant_reply"],
        "evidence_refs": ["maestro:tab:tab_1"],
        "tool_execution_refs": [],
        "authorization_refs": [],
        "findings": [],
        "limitations": [],
        "errors": [],
        "recommended_next_steps": [],
    }
    data.update(overrides)
    return json.dumps(data)


def make_report(status=ExecutionState.COMPLETED, findings=None, tool_refs=None):
    return ExecutionReport(
        workflow_id="wf_1", target_id="target_1", task_id="task_1",
        execution_id="exec_1", agent_id="recon-agent", status=status,
        actions_performed=[],
        evidence_refs=[],
        findings=findings or [],
        tool_execution_refs=tool_refs or [],
    )


def transcript(assistant=result_json(), user="TASK_ID=task_1"):
    return [
        {"role": "user", "content": user},
        {"role": "assistant", "content": assistant},
    ]


def criteria(**kwargs):
    base = dict(expected_agent_id="recon-agent", min_assistant_messages=1)
    base.update(kwargs)
    return AcceptanceCriteria(**base)


def test_acknowledgment_only_is_rejected():
    result = CompletionVerifier().verify(
        make_report(), make_ack(), criteria(), transcript(assistant="READY"), "recon-agent"
    )
    assert result.decision == VerificationDecision.REJECT
    assert result.checks["not_ack_only"] is False
    assert result.checks["structured_result"] is False


def test_marker_only_is_rejected():
    result = CompletionVerifier().verify(
        make_report(), make_ack(), criteria(),
        transcript(assistant="ARCANUM_EXEC_OK task_id=task_1"), "recon-agent"
    )
    assert result.decision == VerificationDecision.REJECT
    assert result.checks["structured_result"] is False


def test_marker_in_refusal_is_rejected():
    result = CompletionVerifier().verify(
        make_report(), make_ack(), criteria(),
        transcript(assistant=(
            "I cannot return ARCANUM_EXEC_OK because no real task was executed"
        )), "recon-agent"
    )
    assert result.decision == VerificationDecision.REJECT
    assert result.checks["not_refusal"] is False
    assert result.checks["structured_result"] is False


def test_structured_result_is_accepted():
    result = CompletionVerifier().verify(
        make_report(), make_ack(), criteria(), transcript(), "recon-agent"
    )
    assert result.decision == VerificationDecision.ACCEPT
    assert result.checks["structured_result"] is True
    assert result.checks["structured_matches_task"] is True


def test_trusted_identity_mismatch_rejected():
    result = CompletionVerifier().verify(
        make_report(), make_ack(), criteria(expected_agent_id="hunt-agent"),
        transcript(), "recon-agent"
    )
    assert result.decision == VerificationDecision.REJECT
    assert result.checks["trusted_identity"] is True
    assert result.checks["expected_agent"] is False


def test_findings_require_validation():
    result = CompletionVerifier().verify(
        make_report(), make_ack(), criteria(),
        transcript(assistant=result_json(findings=["finding_1"])), "recon-agent"
    )
    assert result.decision == VerificationDecision.NEEDS_VALIDATION


def test_tool_records_not_universally_required():
    result = CompletionVerifier().verify(
        make_report(), make_ack(), criteria(), transcript(), "recon-agent"
    )
    assert result.decision == VerificationDecision.ACCEPT
    assert result.checks["tool_execution"] is True


def test_tool_records_required_when_task_needs_them():
    result = CompletionVerifier().verify(
        make_report(), make_ack(), criteria(require_tool_execution=True),
        transcript(), "recon-agent"
    )
    assert result.decision == VerificationDecision.REJECT
    assert result.checks["tool_execution"] is False

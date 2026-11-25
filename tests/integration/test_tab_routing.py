import json

import pytest

from arcanum.core.execution import (
    AcceptanceCriteria,
    DispatchAcknowledgment,
    ExecutionReport,
    ExecutionState,
    VerificationDecision,
)
from arcanum.core.verification import CompletionVerifier
from arcanum.runtime import maestro
from arcanum.runtime.tab_registry import TabRegistry


# ---------------------------------------------------------------------------
# Dispatch command construction
# ---------------------------------------------------------------------------
def test_dispatch_creates_tab_then_reuses_with_queue(tmp_path, monkeypatch):
    calls = []
    issued = {"n": 0}

    def fake_json(args, timeout=120):
        calls.append(list(args))
        if "--tab" in args:
            tab = args[args.index("--tab") + 1]
        else:
            issued["n"] += 1
            tab = f"specialist_tab_{issued['n']}"
        return {
            "success": True,
            "tabId": tab,
            "sessionId": "sess_1",
            "callbackId": "cb_runtime",
        }

    monkeypatch.setattr(maestro, "_json", fake_json)
    monkeypatch.setattr(maestro, "resolve_agent", lambda tab_id: "recon-agent")
    monkeypatch.setattr(maestro, "resolve_tab", lambda tab_id: {
        "tabId": tab_id, "agentName": "recon-agent",
    })
    monkeypatch.setattr(maestro, "get_transcript", lambda tab_id: ["m1", "m2", "m3"])
    monkeypatch.setattr(
        maestro, "ensure_lead_tab", lambda wf, target, reg=None: "lead_tab_1"
    )

    reg = TabRegistry(tmp_path / "tabs.sqlite")

    first = maestro.dispatch(
        agent="recon-agent", prompt="task one", task_id="task_1",
        execution_id="exec_1", workflow_id="wf_1", target_id="target_a",
        registry=reg,
    )
    first_args = calls[-1]
    assert "--new-tab" in first_args
    assert "--visible" in first_args
    assert "--tab" not in first_args
    assert "--queue" not in first_args
    assert "--callback-tab" in first_args
    assert first_args[first_args.index("--callback-tab") + 1] == "lead_tab_1"
    prompt = first_args[first_args.index("--callback-prompt") + 1]
    assert "workflow_id=wf_1" in prompt
    assert "target_id=target_a" in prompt
    assert "task_id=task_1" in prompt
    assert "execution_id=exec_1" in prompt
    assert "lead_tab_id=" in prompt
    assert "specialist_tab_id={{DISPATCH_TAB_ID}}" in prompt
    assert first.tab_reused is False
    assert first.specialist_tab_id == "specialist_tab_1"
    assert reg.get_specialist("wf_1", "target_a", "recon-agent")["tab_id"] == first.tab_id

    second = maestro.dispatch(
        agent="recon-agent", prompt="task two", task_id="task_2",
        execution_id="exec_2", workflow_id="wf_1", target_id="target_a",
        registry=reg,
    )
    second_args = calls[-1]
    assert "--tab" in second_args
    assert second_args[second_args.index("--tab") + 1] == "specialist_tab_1"
    assert "--queue" in second_args
    assert "--new-tab" not in second_args
    assert "--visible" not in second_args
    assert second.tab_reused is True
    assert second.transcript_cursor == 3  # recorded before the new message
    assert second.tab_id == first.tab_id

    third = maestro.dispatch(
        agent="recon-agent", prompt="other target", task_id="task_3",
        execution_id="exec_3", workflow_id="wf_1", target_id="target_b",
        registry=reg,
    )
    assert third.tab_id != first.tab_id
    assert third.tab_reused is False
    assert reg.get_specialist("wf_1", "target_b", "recon-agent")["tab_id"] == third.tab_id
    reg.close()


def test_lead_tab_is_target_scoped(tmp_path, monkeypatch):
    calls = []

    def fake_json(args, timeout=120):
        calls.append(list(args))
        return {"success": True, "tabId": "lead_tab_1", "sessionId": "s"}

    monkeypatch.setattr(maestro, "_json", fake_json)
    monkeypatch.setattr(maestro, "resolve_agent", lambda tab_id: "hackbot-lead")
    monkeypatch.setattr(maestro, "resolve_tab", lambda tab_id: {
        "tabId": tab_id, "agentName": "hackbot-lead",
    })

    reg = TabRegistry(tmp_path / "tabs.sqlite")
    one = maestro.ensure_lead_tab("wf_lead", "target_a", reg)
    two = maestro.ensure_lead_tab("wf_lead", "target_a", reg)
    assert one == two == "lead_tab_1"
    assert sum(1 for c in calls if c[0] == "dispatch") == 1
    assert reg.count_for_workflow("wf_lead") == 1

    # A second target must receive a different Lead tab.
    issued = {"n": 0}

    def fake_json2(args, timeout=120):
        issued["n"] += 1
        return {"success": True, "tabId": f"lead_tab_{issued['n'] + 1}", "sessionId": "s"}

    monkeypatch.setattr(maestro, "_json", fake_json2)
    other = maestro.ensure_lead_tab("wf_lead", "target_b", reg)
    assert other != one
    assert reg.get_lead("wf_lead", "target_b")["tab_id"] == other
    assert reg.count_for_workflow("wf_lead") == 2
    reg.close()


# ---------------------------------------------------------------------------
# Follow-up routing validation
# ---------------------------------------------------------------------------
def test_followup_rejects_unregistered_or_wrong_target_tabs(tmp_path, monkeypatch):
    reg = TabRegistry(tmp_path / "tabs.sqlite")
    reg.put_specialist("wf_1", "target_a", "recon-agent", "tab_a")
    reg.put_specialist("wf_1", "target_b", "recon-agent", "tab_b")

    monkeypatch.setattr(maestro, "resolve_tab", lambda tab_id: {
        "tabId": tab_id, "agentName": "recon-agent",
    })
    monkeypatch.setattr(maestro, "get_transcript", lambda tab_id: [])
    calls = []
    monkeypatch.setattr(maestro, "_json", lambda args, timeout=120: (
        calls.append(list(args)) or {"success": True, "tabId": "tab_a"}
    ))

    ok = maestro.send_followup(
        "tab_a", "clarification", agent="recon-agent", registry=reg,
        workflow_id="wf_1", target_id="target_a",
        task_id="task_1", execution_id="exec_1",
    )
    assert ok["tab_id"] == "tab_a"
    assert calls[-1][:3] == ["dispatch", "--tab", "tab_a"]
    assert "--queue" in calls[-1]
    assert "recon-agent" in calls[-1]

    with pytest.raises(RuntimeError, match="unregistered tab"):
        maestro.send_followup(
            "tab_a", "wrong target", agent="recon-agent", registry=reg,
            workflow_id="wf_1", target_id="target_b",
        )
    with pytest.raises(RuntimeError, match="unregistered tab"):
        maestro.send_followup(
            "tab_b", "wrong target", agent="recon-agent", registry=reg,
            workflow_id="wf_1", target_id="target_a",
        )
    reg.close()


# ---------------------------------------------------------------------------
# Transcript boundary isolation
# ---------------------------------------------------------------------------
def _result(task="task_1", execution="exec_1", target="target_a", status="completed"):
    return json.dumps({
        "task_id": task,
        "execution_id": execution,
        "workflow_id": "wf_1",
        "target_id": target,
        "status": status,
        "actions_performed": ["parse task brief"],
        "evidence_refs": [],
        "findings": [],
    })


def test_transcript_cursor_and_target_matching(monkeypatch):
    transcript = [
        {"role": "user", "content": "TASK_ID=task_1"},
        {"role": "assistant", "content": _result(target="target_a", status="failed")},
        {"role": "assistant", "content": _result(target="target_a", status="completed")},
    ]
    monkeypatch.setattr(maestro, "get_transcript", lambda tab_id: transcript)

    sliced = maestro.wait_for_structured_result(
        "tab_a", "task_1", "exec_1", cursor=1,
        workflow_id="wf_1", target_id="target_a", timeout=1, poll=1,
    )
    assert sliced == transcript[1:]
    assert "completed" in sliced[-1]["content"]

    # Without the boundary the stale result would be the first match; the
    # boundary is what isolates the current execution in a persistent tab.
    unbounded = maestro.wait_for_structured_result(
        "tab_a", "task_1", "exec_1", cursor=0,
        workflow_id="wf_1", target_id="target_a", timeout=1, poll=1,
    )
    assert "failed" in unbounded[1]["content"]

    # A result for another target must not satisfy this execution.
    with pytest.raises(TimeoutError):
        maestro.wait_for_structured_result(
            "tab_a", "task_1", "exec_1", cursor=1,
            workflow_id="wf_1", target_id="target_b", timeout=1, poll=1,
        )

    # A mismatched execution ID is never accepted.
    with pytest.raises(TimeoutError):
        maestro.wait_for_structured_result(
            "tab_a", "task_1", "exec_other", cursor=0,
            workflow_id="wf_1", target_id="target_a", timeout=1, poll=1,
        )


def test_callback_correlation_is_target_and_lead_scoped(tmp_path, monkeypatch):
    import re
    from arcanum.runtime import maestro as m

    reg = TabRegistry(tmp_path / "tabs.sqlite")
    reg.put("wf", "a.com", "hackbot-lead", "lead_a")
    reg.put("wf", "b.com", "hackbot-lead", "lead_b")

    payload = (
        "ARCANUM_COMPLETION task_id=task_1 execution_id=exec_1 "
        "target_id=a.com lead_tab_id=lead_a"
    )
    monkeypatch.setattr(m, "list_lead_queue", lambda: [
        {"tabId": "lead_a", "text": payload},
        {"tabId": "lead_b", "text": payload},
    ])
    monkeypatch.setattr(m, "list_tabs", lambda: [])

    assert m.find_callback_tab(
        "task_1", "exec_1", target_id="a.com",
        workflow_id="wf", registry=reg, timeout=1,
    ) == "lead_a"
    # Same payload in the other target's Lead tab is rejected.
    assert m.find_callback_tab(
        "task_1", "exec_1", lead_tab_id="lead_b", target_id="a.com",
        workflow_id="wf", registry=reg, timeout=1,
    ) is None
    # A caller-supplied wrong Lead tab cannot override the registry.
    assert m.find_callback_tab(
        "task_1", "exec_1", lead_tab_id="lead_b", target_id="a.com",
        workflow_id="wf", registry=reg, timeout=1,
    ) is None
    reg.close()


def test_verifier_rejects_cross_target_result():
    ack = DispatchAcknowledgment(
        task_id="task_1", execution_id="exec_1", workflow_id="wf_1",
        target_id="target_a", agent_id="recon-agent", tab_id="tab_a",
    )
    structured = json.loads(_result(target="target_b"))
    report = ExecutionReport(
        workflow_id="wf_1", target_id="target_a", task_id="task_1",
        execution_id="exec_1", agent_id="recon-agent",
        status=ExecutionState.COMPLETED,
    )
    transcript = [
        {"role": "user", "content": "TASK_ID=task_1"},
        {"role": "assistant", "content": json.dumps(structured)},
    ]
    criteria = AcceptanceCriteria(
        expected_agent_id="recon-agent",
        expected_workflow_id="wf_1",
        expected_target_id="target_a",
    )
    result = CompletionVerifier().verify(
        report=report, ack=ack, criteria=criteria,
        transcript=transcript, trusted_agent_id="recon-agent",
    )
    assert result.decision == VerificationDecision.REJECT
    assert result.checks["structured_target_isolated"] is False

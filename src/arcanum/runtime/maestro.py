"""Trusted Maestro runtime adapter.

All identity and execution evidence comes from the installed Maestro CLI, not
from agent-supplied arguments. A tabId proves dispatch initiation only;
completion is established by the tab transcript, callback tab, and the
Completion Verification Gate.

C2.1.1: dispatch is target-isolated and persistent. The specialist tab is
resolved from the TabRegistry by (workflow_id, target_id, agent_id). Existing
tabs are reused with `--tab <id> --queue`; a new tab is created only when no
valid mapping exists. Completion callbacks are routed to the workflow's single
central Lead tab with `--callback-tab <lead-tab-id>`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.execution import DispatchAcknowledgment, DispatchStatus

if TYPE_CHECKING:
    from .tab_registry import TabRegistry

DEFAULT_CLI = os.environ.get("MAESTRO_CLI", str(Path.home() / ".local/bin/maestro-cli"))
HEAD_AGENT = os.environ.get("MAESTRO_HEAD_AGENT", "hackbot-lead")


def cli_path() -> str:
    if not shutil.which(DEFAULT_CLI) and not Path(DEFAULT_CLI).is_file():
        raise RuntimeError(f"Maestro CLI not found: {DEFAULT_CLI}")
    return DEFAULT_CLI


def _run(args: list[str], timeout: int = 120) -> str:
    result = subprocess.run(
        [cli_path(), *args], capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"maestro-cli {' '.join(args[:3])} failed ({result.returncode}): "
            f"{(result.stderr or result.stdout).strip()[:500]}"
        )
    return result.stdout.strip()


def _json(args: list[str], timeout: int = 120) -> dict[str, Any]:
    out = _run(args, timeout=timeout)
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"maestro-cli returned non-JSON output: {out[:300]}") from exc


def list_tabs() -> list[dict[str, Any]]:
    data = _json(["session", "list", "--all", "--json"])
    return list(data.get("sessions", []))


def list_lead_queue() -> list[dict[str, Any]]:
    """Return queued callbacks/messages for the Head Agent from trusted CLI."""
    try:
        data = _json(["queue", "list", "--agent", HEAD_AGENT])
    except Exception:
        return []
    items: list[dict[str, Any]] = []
    for queue in data.get("queues", []):
        items.extend(list(queue.get("items", [])))
    return items


def _find_tab(tab_id: str) -> dict[str, Any] | None:
    for tab in list_tabs():
        if tab.get("tabId") == tab_id:
            return tab
    return None


def resolve_tab(tab_id: str, timeout: int = 15, poll: float = 1.0) -> dict[str, Any]:
    """Resolve a tab from trusted Maestro session records.

    `dispatch` returns a tabId before the tab is always visible in `session
    list`, so retry briefly instead of treating a registration race as failure.
    """
    import time

    deadline = time.monotonic() + timeout
    while True:
        tab = _find_tab(tab_id)
        if tab is not None:
            return tab
        if time.monotonic() >= deadline:
            raise KeyError(
                f"Maestro tab '{tab_id}' was not found in trusted runtime records"
            )
        time.sleep(poll)


def resolve_agent(tab_id: str) -> str:
    """Return the trusted agent name for a tab from Maestro session metadata."""
    tab = resolve_tab(tab_id)
    name = tab.get("agentName")
    if not name:
        raise RuntimeError(f"Maestro tab '{tab_id}' has no agent identity")
    return str(name)


def get_transcript(tab_id: str) -> list[dict[str, Any]]:
    data = _json(["session", "show", tab_id, "--json"])
    if not data.get("success"):
        raise RuntimeError(f"Could not read transcript for tab '{tab_id}'")
    return list(data.get("messages", []))


def transcript_after(
    tab_id: str, cursor: int
) -> list[dict[str, Any]]:
    """Return only messages appended after a recorded transcript boundary."""
    messages = get_transcript(tab_id)
    return messages[max(0, int(cursor)):]


def _structured_field(data: dict[str, Any], name: str) -> Any:
    """Resolve a field recursively (top-level or nested one level deep)."""
    if not isinstance(data, dict):
        return None
    if name in data:
        return data[name]
    for value in data.values():
        if isinstance(value, dict) and name in value:
            return value[name]
    return None


def default_registry(workflow_id: str = "") -> "TabRegistry":
    """Build a registry from settings (used when a caller does not inject one)."""
    from .tab_registry import TabRegistry
    from ..config.settings import Settings

    settings = Settings()
    return TabRegistry(settings.tab_registry_path)


def latest_lead_tab_for_target(
    target_id: str, registry: "TabRegistry | None" = None
) -> str:
    """Return the active Lead tab registered for a specific target.

    No target means no Lead tab: the caller must resolve the target from trusted
    workflow context rather than inventing one.
    """
    if not target_id:
        return ""
    registry = registry or default_registry()
    rows = registry.conn.execute(
        "SELECT workflow_id, tab_id FROM maestro_tabs "
        "WHERE agent_id=? AND target_id=? AND status='active' "
        "ORDER BY last_used_at DESC LIMIT 1",
        (HEAD_AGENT, target_id),
    ).fetchone()
    return str(rows["tab_id"]) if rows else ""


def ensure_lead_tab(
    workflow_id: str, target_id: str, registry: "TabRegistry | None" = None
) -> str:
    """Return the target-specific Lead tab for a workflow, creating it if needed.

    Uses the same uniform `(workflow_id, target_id, agent_id)` registry path as
    every specialist. There is no workflow-only/global Lead tab.
    """
    if not workflow_id or not target_id:
        raise ValueError(
            "C2.1.3 lead tabs require workflow_id and target_id"
        )
    registry = registry or default_registry(workflow_id)
    prompt = (
        "ARCANUM target-specific Lead tab for workflow "
        f"{workflow_id}, target {target_id}. This conversation handles only "
        "this target. Standing by. Do not call tools. Do not create new tabs. "
        "Reply only: LEAD_READY."
    )
    tab_id, _created, _cursor, _raw = get_or_create_agent_tab(
        workflow_id, target_id, HEAD_AGENT, prompt, registry=registry,
        deliver=False,
    )
    return tab_id


def _callback_prompt(
    workflow_id: str,
    target_id: str,
    task_id: str,
    execution_id: str,
    agent_id: str,
    lead_tab_id: str,
    callback_id: str,
) -> str:
    """Callback body carrying full correlation metadata for a single result.

    `{{DISPATCH_TAB_ID}}` is substituted by Maestro with the actual specialist
    tab ID, including for a tab that is created by this dispatch.
    """
    return (
        f"ARCANUM_COMPLETION callback_id={callback_id} "
        f"workflow_id={workflow_id} target_id={target_id} "
        f"task_id={task_id} execution_id={execution_id} "
        f"agent_id={agent_id} specialist_tab_id={{{{DISPATCH_TAB_ID}}}} "
        f"lead_tab_id={lead_tab_id} status={{{{DISPATCH_STATUS}}}} "
        "dispatch_tab={{DISPATCH_TAB_ID}}\n"
        "Read the correlated specialist tab transcript and return the "
        "structured execution report to the Head Agent. Do not create a new "
        "tab, and do not treat this callback as task completion."
    )


def assert_registered_tab(
    workflow_id: str,
    target_id: str,
    agent_id: str,
    tab_id: str,
    registry: "TabRegistry | None" = None,
) -> None:
    """Reject any caller-supplied tab that is not the registered target tab."""
    registry = registry or default_registry(workflow_id)
    mapping = registry.get(workflow_id, target_id, agent_id)
    if not mapping or str(mapping.get("tab_id") or "") != tab_id:
        registry.log_event(
            workflow_id, target_id, agent_id, "refuse_unregistered_tab",
            tab_id, "tab does not match the authoritative registry mapping",
        )
        raise RuntimeError(
            f"Refusing unregistered tab {tab_id} for "
            f"{workflow_id}/{target_id}/{agent_id}"
        )
    if not registry.validate(mapping, agent_id):
        registry.mark_stale(workflow_id, target_id, agent_id)
        registry.log_event(
            workflow_id, target_id, agent_id, "refuse_stale_tab",
            tab_id, "registered tab failed trusted Maestro validation",
        )
        raise RuntimeError(
            f"Registered tab {tab_id} for {workflow_id}/{target_id}/{agent_id} is stale"
        )


def get_or_create_agent_tab(
    workflow_id: str,
    target_id: str,
    agent_id: str,
    prompt: str,
    *,
    registry: "TabRegistry | None" = None,
    visible: bool = True,
    task_id: str = "",
    execution_id: str = "",
    callback_args: list[str] | None = None,
    deliver: bool = True,
) -> tuple[str, bool, int, dict[str, Any]]:
    """The ONLY controlled way to create or reuse any agent tab.

    All five agents — including `hackbot-lead` — use the same uniform key
    `(workflow_id, target_id, agent_id)`. Returns
    `(tab_id, created, transcript_cursor, raw_response)`. `created=True` means a
    genuinely new mapping was needed; all other cases reuse the registered tab
    with `--tab --queue`.
    """
    registry = registry or default_registry(workflow_id)
    mapping = registry.get(workflow_id, target_id, agent_id)
    callback_args = list(callback_args or [])

    if mapping and registry.validate(mapping, agent_id):
        tab_id = str(mapping["tab_id"])
        # Record the boundary BEFORE the new message is delivered.
        cursor = len(get_transcript(tab_id))
        registry.touch(
            workflow_id, target_id, agent_id,
            task_id=task_id, execution_id=execution_id,
        )
        registry.log_event(
            workflow_id, target_id, agent_id, "reuse", tab_id,
            f"valid mapping; task={task_id} execution={execution_id}",
        )
        if not deliver:
            return tab_id, False, cursor, {"success": True, "tabId": tab_id}
        raw = _json(
            ["dispatch", "--tab", tab_id, "--queue", agent_id, prompt, *callback_args],
            timeout=180,
        )
        return tab_id, False, cursor, raw

    if mapping:
        registry.mark_stale(workflow_id, target_id, agent_id)
        registry.log_event(
            workflow_id, target_id, agent_id, "stale_replaced",
            str(mapping.get("tab_id") or ""),
            "registered tab failed validation; creating one replacement",
        )

    new_tab_args = ["dispatch", "--new-tab"]
    if visible:
        new_tab_args.append("--visible")
    new_tab_args += [agent_id, prompt, *callback_args]
    raw = _json(new_tab_args, timeout=180)
    if not raw.get("success"):
        raise RuntimeError(f"Maestro dispatch failed: {raw}")
    tab_id = str(raw.get("tabId") or raw.get("sessionId") or "")
    if not tab_id:
        raise RuntimeError(f"Maestro dispatch returned no tabId: {raw}")
    trusted = resolve_agent(tab_id)
    if trusted != agent_id:
        raise RuntimeError(
            f"Agent identity mismatch: expected {agent_id}, got {trusted}"
        )
    registry.put(
        workflow_id, target_id, agent_id, tab_id,
        task_id=task_id, execution_id=execution_id,
    )
    registry.log_event(
        workflow_id, target_id, agent_id, "create", tab_id,
        f"no valid mapping; task={task_id} execution={execution_id}",
    )
    return tab_id, True, 0, raw


def dispatch(
    agent: str,
    prompt: str,
    task_id: str,
    execution_id: str,
    workflow_id: str = "",
    target_id: str = "",
    visible: bool = True,
    notify_head: bool = True,
    callback_prompt: str | None = None,
    registry: "TabRegistry | None" = None,
    lead_tab_id: str = "",
) -> DispatchAcknowledgment:
    """Dispatch one specialist task through the single registry-aware path."""
    if not workflow_id or not target_id:
        raise ValueError("C2.1.2 dispatch requires workflow_id and target_id")
    registry = registry or default_registry(workflow_id)

    callback_id = f"cb_{uuid.uuid4().hex[:12]}"
    if notify_head:
        if lead_tab_id:
            lead_mapping = registry.get(workflow_id, target_id, HEAD_AGENT)
            if not lead_mapping or str(lead_mapping.get("tab_id") or "") != lead_tab_id:
                raise RuntimeError(
                    f"Refusing unregistered Lead tab {lead_tab_id} for "
                    f"{workflow_id}/{target_id}"
                )
        lead_tab_id = lead_tab_id or ensure_lead_tab(workflow_id, target_id, registry)
        cb = callback_prompt or _callback_prompt(
            workflow_id, target_id, task_id, execution_id, agent,
            lead_tab_id, callback_id,
        )
        callback_args = [
            "--notify-on-complete", HEAD_AGENT,
            "--callback-tab", lead_tab_id,
            "--callback-prompt", cb,
        ]
    else:
        cb = callback_prompt or ""
        callback_args = []

    tab_id, created, cursor, raw = get_or_create_agent_tab(
        workflow_id, target_id, agent, prompt,
        registry=registry,
        visible=visible,
        task_id=task_id,
        execution_id=execution_id,
        callback_args=callback_args,
    )
    if not raw.get("success"):
        raise RuntimeError(f"Maestro dispatch failed: {raw}")

    trusted_agent = resolve_agent(tab_id)
    return DispatchAcknowledgment(
        task_id=task_id,
        execution_id=execution_id,
        workflow_id=workflow_id,
        target_id=target_id,
        agent_id=trusted_agent,
        tab_id=tab_id,
        agent_session_id=str(raw.get("sessionId") or ""),
        callback_id=str(raw.get("callbackId") or callback_id),
        specialist_tab_id=tab_id,
        lead_tab_id=lead_tab_id,
        transcript_cursor=cursor,
        tab_reused=not created,
        callback_prompt=cb,
        status=DispatchStatus.DISPATCHED,
        raw=raw,
    )


def find_callback_tab(
    task_id: str,
    execution_id: str,
    lead_tab_id: str | None = None,
    target_id: str | None = None,
    timeout: int = 180,
    poll: int = 3,
    workflow_id: str = "",
    registry: "TabRegistry | None" = None,
) -> str | None:
    """Find the target-specific Lead tab correlated to one execution.

    Cross-target callbacks are rejected: the callback tab must be the Lead tab
    registered for `(workflow_id, target_id, hackbot-lead)` and the callback
    payload must carry that same `lead_tab_id`.
    """
    import time

    expected_lead = lead_tab_id or ""
    if workflow_id and target_id:
        reg = registry or default_registry(workflow_id)
        mapping = reg.get(workflow_id, target_id, HEAD_AGENT)
        if not mapping or mapping.get("status") != "active":
            return None
        expected_lead = str(mapping["tab_id"])
        if lead_tab_id and lead_tab_id != expected_lead:
            return None

    def accepted(tab_id: str, text: str) -> bool:
        if expected_lead and tab_id != expected_lead:
            return False
        if not all(k in text for k in ("ARCANUM_COMPLETION", task_id, execution_id)):
            return False
        if target_id and f"target_id={target_id}" not in text:
            return False
        marker = re.search(r"lead_tab_id=([0-9a-fA-F-]{36})", text)
        if marker and marker.group(1) != expected_lead:
            return False
        return True

    deadline = time.monotonic() + timeout
    while True:
        # A callback aimed at a busy Lead tab is queued by Maestro rather than
        # appearing in the transcript immediately.
        for item in list_lead_queue():
            tab_id = str(item.get("tabId") or "")
            if accepted(tab_id, str(item.get("text") or "")):
                return tab_id
        for tab in list_tabs():
            if tab.get("agentName") != HEAD_AGENT:
                continue
            tab_id = str(tab.get("tabId") or "")
            if not tab_id or not expected_lead or tab_id != expected_lead:
                continue
            try:
                transcript = get_transcript(tab_id)
            except Exception:
                continue
            if accepted(tab_id, "\n".join(str(m.get("content", "")) for m in transcript)):
                return tab_id
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll)


def wait_for_status(
    tab_id: str,
    marker: str | None = None,
    timeout: int = 300,
    poll: int = 5,
) -> list[dict[str, Any]]:
    """Poll trusted transcript until assistant output contains a marker."""
    import time

    transcript: list[dict[str, Any]] = []
    for _ in range(max(1, timeout // poll)):
        transcript = get_transcript(tab_id)
        text = "\n".join(
            str(m.get("content", ""))
            for m in transcript
            if m.get("role") == "assistant"
        )
        if marker is None or marker in text:
            return transcript
        time.sleep(poll)
    raise TimeoutError(
        f"Tab '{tab_id}' did not produce marker '{marker}' within {timeout}s"
    )


def wait_for_structured_result(
    tab_id: str,
    task_id: str,
    execution_id: str,
    cursor: int = 0,
    workflow_id: str = "",
    target_id: str = "",
    timeout: int = 300,
    poll: int = 5,
) -> list[dict[str, Any]]:
    """Poll a persistent tab for the structured result of one execution.

    Only messages after the recorded transcript boundary are considered, and
    the result must match the exact workflow, target, task and execution IDs.
    Stale results from earlier tasks in the same tab are rejected.
    """
    import time
    from ..core.verification import parse_structured_result

    for _ in range(max(1, timeout // poll)):
        transcript = transcript_after(tab_id, cursor)
        for message in transcript:
            if message.get("role") != "assistant":
                continue
            data = parse_structured_result(str(message.get("content", "")))
            if not data:
                continue
            if str(data.get("task_id", "")) != task_id:
                continue
            if str(data.get("execution_id", "")) != execution_id:
                continue
            if workflow_id and str(_structured_field(data, "workflow_id") or "") != workflow_id:
                continue
            if target_id:
                target_value = str(
                    _structured_field(data, "target_id")
                    or _structured_field(data, "authorized_target")
                    or ""
                )
                if target_value != target_id:
                    continue
            return transcript
        time.sleep(poll)
    raise TimeoutError(
        f"Tab '{tab_id}' did not produce a matching structured result for "
        f"{workflow_id}/{target_id}/{task_id}/{execution_id} after cursor {cursor}"
    )


def send_followup(
    tab_id: str,
    message: str,
    agent: str = "",
    registry: "TabRegistry | None" = None,
    workflow_id: str = "",
    target_id: str = "",
    task_id: str = "",
    execution_id: str = "",
) -> dict[str, Any]:
    """Send a correlated follow-up to the same target-specific tab.

    A registry mapping is validated before the tab is used; a tab belonging to
    another target or agent is never accepted.
    """
    tab = resolve_tab(tab_id)
    trusted_agent = str(tab.get("agentName") or "")
    if not trusted_agent:
        raise RuntimeError(f"Maestro tab '{tab_id}' has no trusted agent identity")
    if agent and agent != trusted_agent:
        raise RuntimeError(
            f"Follow-up agent mismatch: expected {agent}, tab belongs to {trusted_agent}"
        )

    if registry is not None and workflow_id and target_id:
        assert_registered_tab(
            workflow_id, target_id, trusted_agent, tab_id, registry
        )
        registry.log_event(
            workflow_id, target_id, trusted_agent, "followup", tab_id,
            f"task={task_id} execution={execution_id}",
        )
        registry.touch(
            workflow_id, target_id, trusted_agent,
            task_id=task_id, execution_id=execution_id,
        )

    cursor = len(get_transcript(tab_id))
    raw = _json(
        ["dispatch", "--tab", tab_id, "--queue", trusted_agent, message],
        timeout=180,
    )
    if not raw.get("success"):
        raise RuntimeError(f"Maestro follow-up failed: {raw}")
    return {
        "tab_id": tab_id,
        "agent": trusted_agent,
        "transcript_cursor": cursor,
        "raw": raw,
    }

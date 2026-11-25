"""Deterministic Completion Verification Gate.

The gate validates task-specific acceptance criteria against trusted runtime
records (Maestro tab metadata, transcript, callback, and the execution report).
It never accepts an acknowledgment, marker, or refusal as execution.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .execution import (
    AcceptanceCriteria,
    DispatchAcknowledgment,
    ExecutionReport,
    VerificationDecision,
    VerificationResult,
)

ACKNOWLEDGMENT_WORDS = {
    "ready", "ok", "ack", "received", "accepted_no_tools", "will do", "done"
}
REFUSAL_PATTERNS = (
    re.compile(r"\b(cannot|can't|unable to|refuse|refused|won't|will not)\b", re.I),
    re.compile(r"\b(not a real task|no actual task|did not (?:execute|receive)|do not have)\b", re.I),
)


def parse_structured_result(text: str) -> dict[str, Any] | None:
    """Public parser for structured execution/result JSON in assistant text."""
    return _extract_json(text)


def _matches_term(term: str, values: list[Any]) -> bool:
    """Match a required term against structured values without exact phrasing."""
    t = term.strip().lower().replace("_", " ").replace("-", " ")
    tokens = [x for x in t.split() if len(x) > 2]
    for value in values:
        v = str(value).lower().replace("_", " ").replace("-", " ")
        if t in v:
            return True
        if tokens and all(token in v for token in tokens):
            return True
    return False


def _extract_json(text: str) -> dict[str, Any] | None:
    """Return the most task-relevant JSON object embedded in text, if any."""
    candidates: list[str] = [text.strip()]
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.S | re.I):
        candidates.append(match.group(1).strip())

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(json.dumps(value))

    best: dict[str, Any] | None = None
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        if value.get("task_id") and value.get("execution_id") and value.get("status"):
            return value
        if best is None:
            best = value
    return best


class CompletionVerifier:
    def verify(
        self,
        report: ExecutionReport,
        ack: DispatchAcknowledgment,
        criteria: AcceptanceCriteria,
        transcript: list[dict[str, Any]],
        trusted_agent_id: str,
    ) -> VerificationResult:
        checks: dict[str, bool] = {}
        reasons: list[str] = []

        assistant = [m for m in transcript if m.get("role") == "assistant"]
        user = [m for m in transcript if m.get("role") == "user"]
        assistant_text = "\n".join(str(m.get("content", "")) for m in assistant)
        user_text = "\n".join(str(m.get("content", "")) for m in user)
        structured = _extract_json(assistant_text)

        def field_value(field: str) -> Any:
            """Resolve a structured field recursively (nested objects/lists)."""
            if not structured:
                return None
            stack: list[Any] = [structured]
            while stack:
                current = stack.pop()
                if isinstance(current, dict):
                    if field in current:
                        return current[field]
                    stack.extend(current.values())
                elif isinstance(current, list):
                    stack.extend(current)
            return None

        checks["structured_result"] = structured is not None
        if not checks["structured_result"]:
            reasons.append("No structured execution result found in assistant transcript")

        checks["report_matches_task"] = (
            report.task_id == ack.task_id
            and report.execution_id == ack.execution_id
            and report.workflow_id == ack.workflow_id
            and report.target_id == ack.target_id
        )
        if structured:
            checks["structured_matches_task"] = (
                str(structured.get("task_id", "")) == ack.task_id
                and str(structured.get("execution_id", "")) == ack.execution_id
            )
            if not checks["structured_matches_task"]:
                reasons.append("Structured result task/execution identifiers mismatch")
        else:
            checks["structured_matches_task"] = False
        if not checks["report_matches_task"]:
            reasons.append("Report identifiers do not match the dispatch acknowledgment")

        # C2.1.1 strict cross-target isolation. A result from another target or
        # workflow can never satisfy the current execution, even in a persistent
        # tab that contains many tasks.
        if criteria.expected_workflow_id:
            actual_wf = str(field_value("workflow_id") or "")
            checks["structured_workflow_isolated"] = (
                actual_wf == criteria.expected_workflow_id == ack.workflow_id
            )
            if not checks["structured_workflow_isolated"]:
                reasons.append(
                    "Structured result workflow_id does not match this execution"
                )
        if criteria.expected_target_id:
            actual_target = str(
                field_value("target_id") or field_value("authorized_target") or ""
            )
            checks["structured_target_isolated"] = (
                actual_target == criteria.expected_target_id == ack.target_id
            )
            if not checks["structured_target_isolated"]:
                reasons.append(
                    "Structured result target_id does not match this execution"
                )

        checks["trusted_identity"] = bool(trusted_agent_id) and (
            report.agent_id == trusted_agent_id == ack.agent_id
        )
        if not checks["trusted_identity"]:
            reasons.append(
                f"Agent identity mismatch: report={report.agent_id}, "
                f"ack={ack.agent_id}, trusted runtime={trusted_agent_id}"
            )

        if criteria.expected_agent_id:
            checks["expected_agent"] = trusted_agent_id == criteria.expected_agent_id
            if not checks["expected_agent"]:
                reasons.append("Trusted runtime agent is not the expected specialist")

        checks["tab_correlated"] = bool(ack.tab_id) and (
            not trusted_agent_id or ack.agent_id == trusted_agent_id
        )

        for field in criteria.required_structured_fields:
            checks[f"field:{field}"] = bool(field_value(field))
            if not checks[f"field:{field}"]:
                reasons.append(f"Required structured field '{field}' missing")

        for field, expected in criteria.required_field_values.items():
            actual = str(field_value(field) or "")
            key = f"field_value:{field}"
            checks[key] = _matches_term(expected, [actual])
            if not checks[key]:
                reasons.append(f"Structured field '{field}' does not match required value")

        structured_status = str(structured.get("status", "")).lower() if structured else ""
        checks["status_completed"] = (
            report.status.value == "completed" and structured_status == "completed"
        )
        if not checks["status_completed"]:
            reasons.append(
                f"Execution status not completed in trusted result "
                f"(report={report.status.value}, structured={structured_status or 'missing'})"
            )

        checks["transcript_has_task"] = report.task_id in user_text
        if not checks["transcript_has_task"]:
            reasons.append("Specialist transcript does not contain the task_id")

        checks["assistant_count"] = len(assistant) >= criteria.min_assistant_messages
        if not checks["assistant_count"]:
            reasons.append(
                f"Only {len(assistant)} assistant message(s); "
                f"required {criteria.min_assistant_messages}"
            )

        if criteria.reject_acknowledgment_only:
            normalized = assistant_text.strip().lower().rstrip(".! ")
            checks["not_ack_only"] = normalized not in ACKNOWLEDGMENT_WORDS
            if not checks["not_ack_only"]:
                reasons.append("Specialist returned acknowledgment only, not execution output")

        if checks["structured_result"] and checks["status_completed"]:
            checks["not_refusal"] = True
        else:
            checks["not_refusal"] = not any(p.search(assistant_text) for p in REFUSAL_PATTERNS)
            if not checks["not_refusal"]:
                reasons.append("Assistant response is a refusal or inability statement, not execution evidence")

        structured_actions = structured.get("actions_performed", []) if structured else []
        if not isinstance(structured_actions, list):
            structured_actions = []
        structured_evidence = structured.get("evidence_refs", []) if structured else []
        if not isinstance(structured_evidence, list):
            structured_evidence = []
        structured_tools = structured.get("tool_execution_refs", []) if structured else []
        if not isinstance(structured_tools, list):
            structured_tools = []
        structured_auth = structured.get("authorization_refs", []) if structured else []
        if not isinstance(structured_auth, list):
            structured_auth = []

        for action in criteria.required_actions:
            key = f"action:{action}"
            checks[key] = _matches_term(action, structured_actions + report.actions_performed)
            if not checks[key]:
                reasons.append(f"Required action '{action}' not evidenced")

        for ref in criteria.required_evidence_refs:
            key = f"evidence:{ref}"
            ref_id = ref.split(":", 1)[-1].split("/", 1)[0]
            checks[key] = _matches_term(
                ref_id, structured_evidence + report.evidence_refs
            )
            if not checks[key]:
                reasons.append(f"Required evidence reference '{ref}' missing")

        if criteria.require_tool_execution:
            checks["tool_execution"] = bool(structured_tools) and bool(structured_auth)
            if not checks["tool_execution"]:
                reasons.append("Task requires trusted tool execution records and grants")
        else:
            checks["tool_execution"] = True

        failed = [k for k, v in checks.items() if not v]
        if failed:
            return VerificationResult(
                decision=VerificationDecision.REJECT,
                reasons=reasons or [f"Failed checks: {', '.join(failed)}"],
                checks=checks,
            )

        findings = structured.get("findings", []) if structured else report.findings
        if findings:
            decision = VerificationDecision.NEEDS_VALIDATION
            reasons.append("Findings present; validation phase required")
        else:
            decision = VerificationDecision.ACCEPT
            reasons.append("Task-specific acceptance criteria satisfied by runtime evidence")

        return VerificationResult(decision=decision, reasons=reasons, checks=checks)

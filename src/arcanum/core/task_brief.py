"""Complete pre-dispatch task brief for a specialist."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .execution import AcceptanceCriteria


class TaskBrief(BaseModel):
    workflow_id: str
    target_id: str
    task_id: str
    execution_id: str = ""
    parent_task_id: str = ""
    assigned_agent: str
    objective: str
    authorized_target: str
    scope_ref: str
    operation_constraints: list[str] = Field(default_factory=list)
    available_capabilities: list[str] = Field(default_factory=list)
    relevant_findings: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    expected_activities: list[str] = Field(default_factory=list)
    expected_deliverables: list[str] = Field(default_factory=list)
    acceptance_criteria: AcceptanceCriteria = Field(default_factory=AcceptanceCriteria)
    required_evidence: list[str] = Field(default_factory=list)
    expected_output: str = "ARCANUM_RESULT_JSON"

    def completeness_errors(self) -> list[str]:
        errors = []
        for name in ("workflow_id", "target_id", "task_id", "execution_id",
                     "assigned_agent", "objective", "authorized_target", "scope_ref"):
            if not getattr(self, name):
                errors.append(f"missing {name}")
        if not self.expected_activities:
            errors.append("missing expected_activities")
        if not self.expected_deliverables:
            errors.append("missing expected_deliverables")
        if not self.required_evidence and not self.acceptance_criteria.required_evidence_refs:
            errors.append("missing required_evidence")
        return errors

    def to_prompt(self) -> str:
        lines = [
            "ARCANUM TASK BRIEF — execute the assignment independently.",
            f"workflow_id={self.workflow_id}",
            f"target_id={self.target_id}",
            f"task_id={self.task_id}",
            f"execution_id={self.execution_id}",
        ]
        if self.parent_task_id:
            lines.append(f"parent_task_id={self.parent_task_id}")
        lines += [
            f"assigned_agent={self.assigned_agent}",
            (
                "dpm_agent_id=" + self.assigned_agent + " — use this exact value in "
                "arcanum_request_capability. Never use a Maestro session UUID, "
                "tab UUID, or provider session ID as agent_id."
            ),
            f"objective={self.objective}",
            f"authorized_target={self.authorized_target}",
            f"scope_ref={self.scope_ref}",
            "operation_constraints=" + ", ".join(self.operation_constraints),
            "available_capabilities=" + ", ".join(self.available_capabilities),
            "relevant_findings=" + ", ".join(self.relevant_findings),
            "evidence_refs=" + ", ".join(self.evidence_refs),
            "expected_activities=" + ", ".join(self.expected_activities),
            "expected_deliverables=" + ", ".join(self.expected_deliverables),
            "required_evidence=" + ", ".join(self.required_evidence),
            f"expected_output={self.expected_output}",
            (
                "Execute the assignment. Request required capabilities through "
                "arcanum_request_capability and use granted arcanum-gateway tools "
                "where the task requires evidence. Then return a JSON object with: "
                "task_id, execution_id, status, actions_performed, evidence_refs, "
                "tool_execution_refs, authorization_refs, findings, limitations, "
                "errors, recommended_next_steps, plus any task-specific fields listed "
                "below. Always include workflow_id, target_id, task_id, execution_id, "
                "and authorized_target. Status must be 'completed' only "
                "when the expected activities and evidence were actually produced; "
                "otherwise use 'blocked' or 'failed'. Do not return READY, a marker, "
                "or a plan-only result as completion."
            ),
        ]
        return "\n".join(lines)

    model_config = {"extra": "forbid"}

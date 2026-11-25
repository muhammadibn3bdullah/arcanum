"""Minimal Phase 1 orchestrator.

Receives an operator objective, creates a task via TaskManager, and dispatches a
structured message to a registered agent. It cannot invoke tools or authorize
permissions. Results are structured messages only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..agents.base_agent import BaseAgent
from ..agents.phase_agent import PhaseAgent
from ..core.execution import ExecutionReport, VerificationResult
from ..core.models import AgentMessage, Task, TaskStatus
from .task_manager import TaskManager

if TYPE_CHECKING:
    from ..permissions.permission_manager import DynamicPermissionManager
    from ..tools.gateway_executor import ToolExecutionResult


class Orchestrator:
    def __init__(self, task_manager: TaskManager,
                 dpm: "DynamicPermissionManager | None" = None):
        self.tasks = task_manager
        self.agents: dict[str, BaseAgent] = {}
        self.dpm = dpm

    def register_agent(self, agent: BaseAgent) -> None:
        self.agents[agent.name] = agent

    def unregister_agent(self, agent_name: str) -> None:
        """Remove an agent and revoke all of its active grants."""
        self.agents.pop(agent_name, None)
        if self.dpm is not None:
            self.dpm.revoke_for_agent(agent_name)

    def receive_objective(self, objective: str, operator_id: str,
                          task_type: str = "general") -> Task:
        return self.tasks.create_task(objective, operator_id, task_type)

    def dispatch(self, task: Task, agent_name: str) -> AgentMessage:
        """Send a self-contained task message to one agent; return its result.

        No full conversation history is forwarded. No tool execution occurs.
        """
        agent = self.agents.get(agent_name)
        if agent is None:
            raise KeyError(f"Agent '{agent_name}' is not registered")

        message = AgentMessage(
            task_id=task.id,
            from_agent="orchestrator",
            to_agent=agent_name,
            objective=task.objective,
            task_type=task.task_type,
            scope_refs=list(task.scope_refs),
            execution_status="pending",
            required_next_steps=["Return a planning summary; do not execute tools"],
        )
        ack = agent.receive_task(message)
        # An acknowledgment is NOT a result. Never write it as task output.
        self.tasks.update_status(task.id, TaskStatus.DISPATCHED)
        return ack

    def record_execution_result(
        self,
        task: Task,
        report: ExecutionReport,
        verification: VerificationResult,
    ) -> VerificationResult:
        """Record a verified execution result; acknowledgment never reaches here."""
        if verification.decision.value != "ACCEPT":
            self.tasks.update_status(task.id, TaskStatus.IN_PROGRESS)
            self.tasks.set_result(
                task.id,
                f"execution {report.execution_id} rejected: {verification.decision.value}",
            )
            return verification
        self.tasks.update_status(task.id, TaskStatus.EXECUTION_COMPLETE)
        self.tasks.set_result(
            task.id,
            f"execution {report.execution_id} accepted: {report.status.value}",
        )
        return verification

    def dispatch_phase(
        self,
        task: Task,
        agent_name: str,
        skill_id: str,
        capability: str,
        operation: str,
        target: str,
        params: dict[str, Any] | None = None,
        risk: str = "LOW",
        task_authorized: bool = True,
        **kwargs: Any,
    ) -> "ToolExecutionResult":
        """Run one specialist phase: load skill, request grant, execute via gateway."""
        agent = self.agents.get(agent_name)
        if agent is None:
            raise KeyError(f"Agent '{agent_name}' is not registered")
        if not isinstance(agent, PhaseAgent):
            raise TypeError(f"Agent '{agent_name}' is not a PhaseAgent")

        agent.load_skill(skill_id)
        result = agent.execute_phase(
            task=task,
            skill_id=skill_id,
            capability=capability,
            operation=operation,
            target=target,
            params=params,
            risk=risk,
            task_authorized=task_authorized,
            **kwargs,
        )
        self.tasks.set_result(
            task.id,
            f"{skill_id}:{capability} -> {result.decision} ({result.reason})",
        )
        return result

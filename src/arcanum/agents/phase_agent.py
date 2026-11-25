"""Phase agent that turns a loaded skill into a gateway-authorized action.

The agent never holds a raw tool handle. It records the loaded skill, creates a
structured ToolOperation (a request, not authorization), and delegates to the
GatewayToolExecutor, which asks the DPM and then executes through the gateway.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..core.models import Task, ToolOperation
from ..tools.gateway_executor import GatewayToolExecutor, ToolExecutionResult
from .base_agent import BaseAgent
from .skill_registry import SkillRegistry

if TYPE_CHECKING:
    from ..llm.base import LLMProvider


class PhaseAgent(BaseAgent):
    def __init__(
        self,
        name: str,
        profile: str,
        executor: GatewayToolExecutor,
        skill_registry: SkillRegistry | None = None,
        llm_provider: "LLMProvider | None" = None,
    ):
        super().__init__(name, profile)
        self.executor = executor
        self.llm_provider = llm_provider
        self.skills = skill_registry or SkillRegistry()
        self.loaded_skills: list[str] = []

    def plan_with_llm(self, prompt: str) -> str:
        """Ask the configured provider for planning text only.

        The return value is untrusted data. It is never used to choose a
        capability, create a grant, or alter a decision.
        """
        if self.llm_provider is None:
            raise RuntimeError("No LLM provider configured for this agent")
        return self.llm_provider.complete(prompt)

    def load_skill(self, skill_id: str) -> str:
        if not self.skills.has(skill_id):
            raise ValueError(f"Skill '{skill_id}' is not installed")
        if skill_id not in self.loaded_skills:
            self.loaded_skills.append(skill_id)
        return skill_id

    def execute_phase(
        self,
        task: Task,
        skill_id: str,
        capability: str,
        operation: str,
        target: str,
        params: dict[str, Any] | None = None,
        risk: str = "LOW",
        task_authorized: bool = False,
        tool_name: str | None = None,
        max_requests: int | None = None,
        allowed_methods: list[str] | None = None,
        rate_limit_per_minute: int | None = None,
    ) -> ToolExecutionResult:
        """Load a skill, request a capability, and execute only if granted.

        `task_authorized` must be supplied by trusted orchestration code. It
        defaults to False so an agent cannot authorize its own task.
        """
        if skill_id not in self.loaded_skills:
            raise PermissionError(
                f"Agent '{self.name}' must load skill '{skill_id}' before using it"
            )

        import uuid

        # Record enough context for an operator approval to re-run this exact
        # phase after granting. The context is still only data; it cannot grant.
        phase_context = {
            "objective": task.objective,
            "skill_id": skill_id,
            "capability": capability,
            "operation": operation,
            "target": target,
            "task_type": task.task_type,
            "risk": risk,
            "params": params or {},
        }
        tool_op = ToolOperation(
            request_id=f"req_{uuid.uuid4().hex[:12]}",
            agent_id=self.name,
            task_id=task.id,
            task_type=task.task_type,
            capability=capability,
            target=target,
            operation=operation,
            justification=f"skill:{skill_id}",
            risk=risk,
            phase_context=phase_context,
        )
        return self.executor.request_and_execute(
            tool_op,
            task_authorized=task_authorized,
            tool_name=tool_name,
            max_requests=max_requests,
            allowed_methods=allowed_methods,
            rate_limit_per_minute=rate_limit_per_minute,
            params=params,
        )

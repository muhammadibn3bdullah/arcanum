"""Agent-facing bridge from structured ToolOperation to the DPM + Gateway.

This module is NOT exposed over MCP. The MCP server only accepts already-issued
grants. Agents use this helper in-process to request a capability and then
execute it — but the agent-supplied grant_id is always ignored, so an agent can
never authorize itself.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..core.models import ToolOperation
from ..permissions.permission_manager import DynamicPermissionManager
from ..permissions.permission_models import PermissionRequest, RiskLevel
from .tool_registry import ToolRegistry


class ToolExecutionResult(BaseModel):
    request_id: str
    tool_name: str
    decision: str
    allowed: bool
    grant_id: str | None = None
    requires_human_approval: bool = False
    reason: str = ""
    result: Any = None
    model_config = {"extra": "forbid"}


class GatewayToolExecutor:
    def __init__(self, dpm: DynamicPermissionManager, registry: ToolRegistry):
        self.dpm = dpm
        self.registry = registry

    def _default_tool(self, capability: str) -> str:
        tools = self.registry.capability_tools(capability)
        if not tools:
            return capability
        return tools[0].name

    def request_and_execute(
        self,
        operation: ToolOperation,
        *,
        task_authorized: bool = False,
        tool_name: str | None = None,
        duration_seconds: int = 300,
        risk: RiskLevel | None = None,
        max_requests: int | None = None,
        allowed_methods: list[str] | None = None,
        rate_limit_per_minute: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> ToolExecutionResult:
        """Request a DPM grant, then execute through the gateway.

        `task_authorized` must be supplied by trusted orchestration code. The
        default is False so an agent cannot accidentally self-authorize.
        """
        name = tool_name or self._default_tool(operation.capability)
        request = PermissionRequest(
            request_id=operation.request_id,
            agent_id=operation.agent_id,
            task_id=operation.task_id,
            requested_capability=operation.capability,
            target=operation.target,
            operation=operation.operation,
            justification=operation.justification,
            duration_seconds=duration_seconds,
            risk=risk or RiskLevel[operation.risk.upper()],
            task_type=operation.task_type,
            max_requests=max_requests,
            allowed_methods=allowed_methods or [],
            rate_limit_per_minute=rate_limit_per_minute,
            phase_context=operation.phase_context,
        )
        # NOTE: operation.grant_id is deliberately ignored. A request is not
        # authorization; only the DPM-issued grant below may be used.
        decision = self.dpm.request_permission(
            request,
            task_authorized=task_authorized,
        )

        base = ToolExecutionResult(
            request_id=operation.request_id,
            tool_name=name,
            decision=decision.decision.value,
            allowed=False,
            grant_id=decision.grant.grant_id if decision.grant else None,
            requires_human_approval=decision.requires_human_approval,
            reason=decision.reason,
        )
        if decision.grant is None:
            return base

        allowed, result, reason = self.registry.execute(
            name=name,
            agent_id=operation.agent_id,
            task_id=operation.task_id,
            target=operation.target,
            operation=operation.operation,
            grant_id=decision.grant.grant_id,
            params=params,
        )
        base.allowed = allowed
        base.result = result
        base.reason = reason
        return base

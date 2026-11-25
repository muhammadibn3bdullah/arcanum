"""Authorization Gateway — mandatory enforcement point for protected tools.

No protected tool executes without passing through this gateway. The gateway
re-verifies the DPM-issued grant, live scope, and grant constraints at the
execution boundary. Every authorization attempt and execution is audited.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from .permission_manager import DynamicPermissionManager
from .permission_models import AuthorizationGrant, PermissionAuditRecord


class AuthorizationGateway:
    def __init__(self, dpm: DynamicPermissionManager):
        self.dpm = dpm
        self._tools: dict[str, dict] = {}

    def register_tool(self, name: str, capability: str, handler: Callable, description: str = "") -> None:
        self._tools[name] = {
            "capability": capability,
            "handler": handler,
            "description": description,
        }

    def list_tools(self) -> list[dict]:
        return [{"name": n, "capability": t["capability"], "description": t["description"]}
                for n, t in self._tools.items()]

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def _tool_capability(self, tool_name: str) -> str | None:
        t = self._tools.get(tool_name)
        return t["capability"] if t else None

    def _audit(
        self,
        event_type: str,
        agent_id: str,
        task_id: str,
        tool_name: str,
        capability: str,
        target: str,
        operation: str,
        grant_id: str | None,
        decision: str,
        reason: str,
    ) -> None:
        self.dpm.audit.record(
            PermissionAuditRecord(
                record_id=f"gw_{uuid.uuid4().hex[:12]}",
                event_type=event_type,
                agent_id=agent_id,
                task_id=task_id,
                requested_capability=capability,
                target=target,
                decision=decision,
                decision_reason=f"{reason} [tool={tool_name}, operation={operation}]",
                grant_id=grant_id,
                policy_ref="authorization_gateway",
                timestamp=datetime.now(timezone.utc),
            )
        )

    def check_authorization(
        self,
        agent_id: str,
        task_id: str,
        tool_name: str,
        target: str,
        operation: str,
        grant_id: str,
    ) -> tuple[bool, str, AuthorizationGrant | None]:
        """Verify authorization without executing. Every attempt is audited."""
        capability = self._tool_capability(tool_name)
        if capability is None:
            reason = f"Tool '{tool_name}' is not registered"
            self._audit("gateway_authorization_attempt", agent_id, task_id, tool_name,
                        tool_name, target, operation, grant_id or None, "DENY", reason)
            return False, reason, None

        valid, reason, grant = self.dpm.validate_grant(
            grant_id, agent_id, task_id, capability, target, operation
        )
        self._audit("gateway_authorization_attempt", agent_id, task_id, tool_name,
                    capability, target, operation, grant_id or None,
                    "ALLOW" if valid else "DENY", reason)
        return valid, reason, grant

    def _constraint_failure(
        self, grant: AuthorizationGrant, params: dict[str, Any]
    ) -> str | None:
        constraints = grant.constraints or {}

        allowed_methods = constraints.get("allowed_methods") or []
        if allowed_methods:
            method = params.get("method") or params.get("http_method")
            if method and str(method).upper() not in {str(m).upper() for m in allowed_methods}:
                return f"Method '{method}' is not allowed by grant constraints"

        rate = constraints.get("rate_limit_per_minute") or 0
        if rate > 0 and grant.last_used_at is not None:
            elapsed = (datetime.now(timezone.utc) - grant.last_used_at).total_seconds()
            if elapsed < 60 / rate:
                return "Grant rate limit exceeded"
        return None

    def execute(
        self,
        tool_name: str,
        agent_id: str,
        task_id: str,
        target: str,
        operation: str,
        grant_id: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[bool, Any, str]:
        """Authorize and execute a protected tool. Returns (allowed, result, reason)."""
        params = params or {}
        capability = self._tool_capability(tool_name)
        allowed, reason, grant = self.check_authorization(
            agent_id, task_id, tool_name, target, operation, grant_id
        )
        if not allowed or grant is None:
            return False, None, reason

        # Live scope re-verification: a scope change revokes the grant.
        scope_ok, reason = self.dpm.verify_scope_for_execution(grant, target, operation)
        if not scope_ok:
            self._audit("tool_execution", agent_id, task_id, tool_name, capability,
                        target, operation, grant_id, "DENY", reason)
            return False, None, reason

        constraint_reason = self._constraint_failure(grant, params)
        if constraint_reason is not None:
            self.dpm.revoke_grant(grant_id, constraint_reason)
            self._audit("tool_execution", agent_id, task_id, tool_name, capability,
                        target, operation, grant_id, "DENY", constraint_reason)
            return False, None, constraint_reason

        tool = self._tools[tool_name]
        try:
            result = tool["handler"](params)
        except Exception as exc:
            self.dpm.revoke_grant(grant_id, f"tool_exception: {exc}")
            self._audit("tool_execution", agent_id, task_id, tool_name, capability,
                        target, operation, grant_id, "DENY", f"Tool execution failed: {exc}")
            return False, None, f"Tool execution failed: {exc}"

        self.dpm.consume_grant(grant_id)
        self._audit("tool_execution", agent_id, task_id, tool_name, capability,
                    target, operation, grant_id, "ALLOW", "Tool executed successfully")
        return True, result, "Tool executed successfully"

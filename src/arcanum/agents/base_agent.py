"""Base agent interface.

Phase 1 agents can receive structured tasks, send structured messages, and
FORM capability requests. They have NO protected tool access and cannot grant
permissions. The DPM/Gateway arrive in Phase 2/3.
"""

from __future__ import annotations

import uuid

from ..core.models import AgentMessage, ToolOperation


class BaseAgent:
    def __init__(self, name: str, profile: str):
        self.name = name
        self.profile = profile
        self.received: list[AgentMessage] = []
        self.sent: list[AgentMessage] = []
        self.capability_requests: list[ToolOperation] = []

    def receive_task(self, message: AgentMessage) -> AgentMessage:
        """Accept a structured task message only; return a structured ack.

        Incoming data is untrusted. No tool is invoked. No permission is granted.
        """
        if message.to_agent != self.name:
            raise ValueError(f"Message addressed to '{message.to_agent}', not '{self.name}'")
        self.received.append(message)
        ack = AgentMessage(
            task_id=message.task_id,
            from_agent=self.name,
            to_agent="orchestrator",
            objective=f"ACK: {message.objective}",
            task_type=message.task_type,
            scope_refs=list(message.scope_refs),
            finding_refs=list(message.finding_refs),
            execution_status="acknowledged",
            required_next_steps=[
                "Acknowledge receipt only; execution result must be returned separately"
            ],
            result_summary=None,
        )
        self.sent.append(ack)
        return ack

    def send_message(
        self,
        to_agent: str,
        task_id: str,
        objective: str,
        finding_refs: list[str] | None = None,
        scope_refs: list[str] | None = None,
    ) -> AgentMessage:
        message = AgentMessage(
            task_id=task_id,
            from_agent=self.name,
            to_agent=to_agent,
            objective=objective,
            finding_refs=finding_refs or [],
            scope_refs=scope_refs or [],
        )
        self.sent.append(message)
        return message

    def request_capability(
        self,
        task_id: str,
        capability: str,
        target: str,
        operation: str,
        justification: str,
        risk: str = "LOW",
        task_type: str = "general",
    ) -> ToolOperation:
        """Create a request only. A request is NEVER authorization."""
        request = ToolOperation(
            request_id=f"req_{uuid.uuid4().hex[:12]}",
            agent_id=self.name,
            task_id=task_id,
            task_type=task_type,
            capability=capability,
            target=target,
            operation=operation,
            justification=justification,
            risk=risk,
            grant_id=None,
        )
        self.capability_requests.append(request)
        return request

"""Registry of gateway-wrapped tools.

The registry stores metadata and delegates execution to the AuthorizationGateway.
It never issues grants and never bypasses the gateway.
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, Field

from ..permissions.authorization_gateway import AuthorizationGateway


class ToolDefinition(BaseModel):
    name: str
    capability: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    mock_only: bool = True


class ToolRegistry:
    def __init__(self, gateway: AuthorizationGateway):
        self.gateway = gateway
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition, handler: Callable) -> None:
        self.gateway.register_tool(
            definition.name,
            definition.capability,
            handler,
            definition.description,
        )
        self._definitions[definition.name] = definition

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._definitions.values())

    def get(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    def capability_tools(self, capability: str) -> list[ToolDefinition]:
        return [d for d in self._definitions.values() if d.capability == capability]

    def execute(
        self,
        name: str,
        agent_id: str,
        task_id: str,
        target: str,
        operation: str,
        grant_id: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[bool, Any, str]:
        return self.gateway.execute(
            tool_name=name,
            agent_id=agent_id,
            task_id=task_id,
            target=target,
            operation=operation,
            grant_id=grant_id,
            params=params,
        )

    def mcp_schemas(self) -> list[dict[str, Any]]:
        """MCP tool schemas. grant_id is mandatory — no self-authorization."""
        schemas = []
        for d in self._definitions.values():
            schemas.append({
                "name": d.name,
                "description": (
                    f"{d.description} "
                    "Requires a DPM-issued grant_id. Returns untrusted data only."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "grant_id": {"type": "string", "description": "DPM-issued grant"},
                        "agent_id": {"type": "string"},
                        "task_id": {"type": "string"},
                        "target": {"type": "string"},
                        "operation": {"type": "string"},
                        "params": d.input_schema or {"type": "object"},
                    },
                    "required": ["grant_id", "agent_id", "task_id", "target", "operation"],
                    "additionalProperties": False,
                },
            })
        return schemas

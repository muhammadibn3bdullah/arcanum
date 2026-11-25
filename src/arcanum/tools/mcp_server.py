"""arcanum-gateway MCP server (stdlib JSON-RPC over stdio).

Two surfaces are exposed:

1. Gateway-wrapped tools — every call requires a DPM-issued ``grant_id``.
2. ``arcanum_request_capability`` — the DPM request path required by the
   architecture. It accepts a PermissionRequest and returns a decision. It can
   never be used to self-authorize: task authorization is derived from trusted
   scope data in code, and authorization fields supplied by a caller are
   rejected. Human approval still goes through the operator's approval queue.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

from .tool_registry import ToolRegistry

_ALLOWED_TOOL_ARGUMENTS = {
    "grant_id", "agent_id", "task_id", "target", "operation", "params"
}
_ALLOWED_REQUEST_ARGUMENTS = {
    "agent_id", "task_id", "capability", "target", "operation",
    "justification", "risk", "task_type", "duration_seconds",
}
REQUEST_TOOL = "arcanum_request_capability"
VERIFY_TOOL = "arcanum_verify_execution"

REQUEST_TOOL_SCHEMA = {
    "name": REQUEST_TOOL,
    "description": (
        "Submit a permission request to the deterministic Dynamic Permission "
        "Manager. Returns ALLOW with a grant_id, DENY, or REQUIRE_HUMAN_APPROVAL "
        "with an approval_id. Authorization fields are not accepted."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "agent_id": {"type": "string"},
            "task_id": {"type": "string"},
            "capability": {"type": "string"},
            "target": {"type": "string"},
            "operation": {"type": "string"},
            "justification": {"type": "string"},
            "risk": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]},
            "task_type": {"type": "string"},
            "duration_seconds": {"type": "integer"},
        },
        "required": ["agent_id", "task_id", "capability", "target", "operation", "justification"],
        "additionalProperties": False,
    },
}


VERIFY_TOOL_SCHEMA = {
    "name": VERIFY_TOOL,
    "description": (
        "Verify a real Maestro/OpenCode specialist execution before the Head "
        "Agent accepts it. Reads trusted Maestro records; cannot accept an "
        "acknowledgment as completion."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "execution_id": {"type": "string"},
            "workflow_id": {"type": "string"},
            "target_id": {"type": "string"},
            "tab_id": {"type": "string"},
            "expected_agent": {"type": "string"},
            "expected_marker": {"type": "string"},
            "required_actions": {"type": "array", "items": {"type": "string"}},
            "require_tool_execution": {"type": "boolean"},
        },
        "required": ["task_id", "execution_id", "tab_id"],
        "additionalProperties": False,
    },
}


class GatewayMCPServer:
    def __init__(
        self,
        registry: ToolRegistry,
        name: str = "arcanum-gateway",
        permission_requester: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        execution_verifier: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ):
        self.registry = registry
        self.name = name
        self.permission_requester = permission_requester
        self.execution_verifier = execution_verifier

    def _ok(self, request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _error(self, request_id: Any, message: str, code: int = -32600) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    def _tools(self) -> list[dict[str, Any]]:
        tools = self.registry.mcp_schemas()
        if self.permission_requester is not None:
            tools = [REQUEST_TOOL_SCHEMA, *tools]
        if self.execution_verifier is not None:
            tools = [VERIFY_TOOL_SCHEMA, *tools]
        return tools

    def _call_request_tool(
        self, request_id: Any, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if self.permission_requester is None:
            return self._error(request_id, "DPM request path is disabled", -32601)

        unknown = set(arguments) - _ALLOWED_REQUEST_ARGUMENTS
        if unknown:
            return self._error(
                request_id,
                f"Disallowed request arguments: {sorted(unknown)}. "
                "Authorization cannot be supplied by a caller.",
                -32602,
            )
        required = REQUEST_TOOL_SCHEMA["inputSchema"]["required"]
        missing = [k for k in required if not arguments.get(k)]
        if missing:
            return self._error(request_id, f"Missing required arguments: {missing}", -32602)

        result = self.permission_requester(arguments)
        return self._ok(request_id, {
            "content": [{
                "type": "text",
                "text": json.dumps(result),
            }],
            "isError": result.get("decision") == "DENY",
        })

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}

        if method == "initialize":
            return self._ok(request_id, {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": self.name, "version": "0.2.0"},
                "capabilities": {"tools": {}},
            })

        if method == "notifications/initialized":
            return self._ok(request_id, {})

        if method == "tools/list":
            return self._ok(request_id, {"tools": self._tools()})

        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}

            if name == REQUEST_TOOL:
                return self._call_request_tool(request_id, arguments)
            if name == VERIFY_TOOL:
                if self.execution_verifier is None:
                    return self._error(request_id, "Verification is disabled", -32601)
                result = self.execution_verifier(arguments)
                return self._ok(request_id, {
                    "content": [{"type": "text", "text": json.dumps(result)}],
                    "isError": result.get("decision") in {"REJECT", "BLOCKED", "FAILED"},
                })

            if name not in {d.name for d in self.registry.list_tools()}:
                return self._error(request_id, f"Unknown tool '{name}'", -32602)

            unknown = set(arguments) - _ALLOWED_TOOL_ARGUMENTS
            if unknown:
                return self._error(
                    request_id,
                    f"Disallowed tool arguments: {sorted(unknown)}. "
                    "The MCP surface cannot create grants or change authorization.",
                    -32602,
                )

            grant_id = arguments.get("grant_id")
            if not grant_id:
                return self._error(request_id, "grant_id is required", -32602)

            allowed, result, reason = self.registry.execute(
                name=name,
                agent_id=str(arguments.get("agent_id", "")),
                task_id=str(arguments.get("task_id", "")),
                target=str(arguments.get("target", "")),
                operation=str(arguments.get("operation", "")),
                grant_id=str(grant_id),
                params=arguments.get("params") or {},
            )
            return self._ok(request_id, {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "allowed": allowed,
                        "reason": reason,
                        "untrusted_result": result,
                    }),
                }],
                "isError": not allowed,
            })

        return self._error(request_id, f"Method not found: {method}", -32601)

    def serve_stdin_forever(self) -> None:
        """Line-delimited JSON-RPC loop for stdio MCP transport."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                response = self.handle_request(request)
            except Exception as exc:  # never crash the MCP loop on one bad call
                response = self._error(None, f"Invalid request: {exc}", -32700)
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()

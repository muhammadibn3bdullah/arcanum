"""Safe mock tool handlers for Phase 3.

These handlers NEVER touch the network, disk, or real proxy state. They exist to
prove the gateway boundary end-to-end. Real Burp/Caido adapters are injected by
replacing these handlers; the authorization path stays identical.
"""

from __future__ import annotations

from typing import Any

from ..permissions.authorization_gateway import AuthorizationGateway
from ..permissions.permission_models import DEFAULT_PROFILES
from .tool_registry import ToolDefinition, ToolRegistry


class MockCaidoTrafficStore:
    """Deterministic captured-traffic store used by the Caido fingerprint skill."""

    def __init__(self, records: list[dict[str, Any]] | None = None):
        self.records = records or [
            {
                "host": "lab.example.test",
                "path": "/api/v1/users",
                "method": "GET",
                "status": 200,
                "server": "mock-nginx",
                "headers": {"content-type": "application/json"},
            },
            {
                "host": "lab.example.test",
                "path": "/api/v1/health",
                "method": "GET",
                "status": 200,
                "server": "mock-nginx",
                "headers": {"content-type": "text/plain"},
            },
        ]

    def query(self, params: dict[str, Any]) -> dict[str, Any]:
        host = str(params.get("host", "")).lower()
        limit = int(params.get("limit", 50))
        matches = [r for r in self.records if not host or host in r["host"].lower()]
        return {
            "untrusted_data": {
                "source": "mock_caido_store",
                "count": len(matches[:limit]),
                "traffic": matches[:limit],
            }
        }


class MockBurpProxy:
    """In-memory stand-in for Burp intercept / Repeater."""

    def __init__(self):
        self.intercepted: list[dict[str, Any]] = []
        self.repeated: list[dict[str, Any]] = []

    def intercept(self, params: dict[str, Any]) -> dict[str, Any]:
        entry = {
            "request": params.get("request", ""),
            "action": params.get("action", "hold"),
        }
        self.intercepted.append(entry)
        return {"untrusted_data": {"source": "mock_burp", "intercepted": entry}}

    def repeat(self, params: dict[str, Any]) -> dict[str, Any]:
        entry = {
            "request": params.get("request", ""),
            "response": "HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok",
        }
        self.repeated.append(entry)
        return {"untrusted_data": {"source": "mock_burp", "repeated": entry}}


def _generic_mock(capability: str):
    def handler(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "untrusted_data": {
                "source": "mock_tool",
                "capability": capability,
                "params": params,
                "note": "MOCK ONLY - no real external access",
            }
        }
    return handler


def build_mock_registry(
    gateway: AuthorizationGateway,
    caido_store: MockCaidoTrafficStore | None = None,
    burp: MockBurpProxy | None = None,
) -> ToolRegistry:
    """Register one safe mock handler for every profile capability."""
    registry = ToolRegistry(gateway)
    caido = caido_store or MockCaidoTrafficStore()
    proxy = burp or MockBurpProxy()

    specific_handlers: dict[str, Any] = {
        "caido_read": caido.query,
        "caido_traffic_query": caido.query,
        "burp_intercept": proxy.intercept,
        "burp_repeater": proxy.repeat,
    }

    input_schemas: dict[str, dict[str, Any]] = {
        "caido_read": {
            "type": "object",
            "properties": {"host": {"type": "string"}, "limit": {"type": "integer"}},
        },
        "caido_traffic_query": {
            "type": "object",
            "properties": {"host": {"type": "string"}, "limit": {"type": "integer"}},
        },
        "burp_intercept": {
            "type": "object",
            "properties": {"request": {"type": "string"}, "action": {"type": "string"}},
        },
        "burp_repeater": {
            "type": "object",
            "properties": {"request": {"type": "string"}},
        },
    }

    capabilities = sorted({
        cap for profile in DEFAULT_PROFILES.values() for cap in profile.max_capabilities
    })
    for capability in capabilities:
        registry.register(
            ToolDefinition(
                name=capability,
                capability=capability,
                description=f"Mock gateway-wrapped tool for {capability}",
                input_schema=input_schemas.get(capability, {"type": "object"}),
                mock_only=True,
            ),
            specific_handlers.get(capability, _generic_mock(capability)),
        )
    return registry

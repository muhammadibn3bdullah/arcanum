"""Phase 3 — gateway-wrapped Tool Execution Layer.

Agents never receive raw tool handles. Every tool is registered here, exposed
through the AuthorizationGateway, and (optionally) surfaced through the
arcanum-gateway MCP scaffold with a required grant_id.
"""

from .burp_adapter import (
    BurpMCPClient,
    BurpToolAdapter,
    BurpUnavailable,
    install_burp_adapter,
)
from .caido_adapter import (
    CaidoGraphQLClient,
    CaidoToolAdapter,
    CaidoUnavailable,
    install_caido_adapter,
)
from .gateway_executor import GatewayToolExecutor, ToolExecutionResult
from .harness_adapter import (
    HarnessIngestClient,
    HarnessToolAdapter,
    HarnessUnavailable,
    install_harness_adapter,
)
from .mcp_server import GatewayMCPServer
from .mock_tools import MockBurpProxy, MockCaidoTrafficStore, build_mock_registry
from .serve import build_server
from .tool_registry import ToolDefinition, ToolRegistry

__all__ = [
    "BurpMCPClient",
    "BurpToolAdapter",
    "BurpUnavailable",
    "CaidoGraphQLClient",
    "CaidoToolAdapter",
    "CaidoUnavailable",
    "GatewayMCPServer",
    "GatewayToolExecutor",
    "HarnessIngestClient",
    "HarnessToolAdapter",
    "HarnessUnavailable",
    "MockBurpProxy",
    "MockCaidoTrafficStore",
    "ToolDefinition",
    "ToolExecutionResult",
    "ToolRegistry",
    "build_mock_registry",
    "build_server",
    "install_burp_adapter",
    "install_caido_adapter",
    "install_harness_adapter",
]

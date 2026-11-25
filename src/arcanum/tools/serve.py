"""Entry point for the arcanum-gateway MCP server.

Builds the DPM, Authorization Gateway, and tool registry, then serves:

- gateway-wrapped tools (every call requires a DPM-issued grant_id)
- ``arcanum_request_capability`` — the DPM request path from architecture §3

The request path derives task authorization from trusted scope data, so an
agent cannot self-authorize through MCP arguments.
"""

from __future__ import annotations

import argparse
import json
import uuid
from typing import Any

from ..config.settings import Settings
from ..memory.database import Database
from ..permissions.approval_queue import ApprovalQueue
from ..permissions.authorization_gateway import AuthorizationGateway
from ..permissions.permission_audit import PermissionAuditLog
from ..permissions.permission_manager import DynamicPermissionManager, PermissionStore
from ..core.execution import (
    AcceptanceCriteria,
    DispatchAcknowledgment,
    DispatchStatus,
    ExecutionReport,
    ExecutionState,
)
from ..core.verification import CompletionVerifier
from ..permissions.permission_models import PermissionRequest, RiskLevel
from ..runtime import maestro
from .burp_adapter import BurpToolAdapter, install_burp_adapter
from .caido_adapter import CaidoToolAdapter, install_caido_adapter
from .harness_adapter import install_harness_adapter
from .mock_tools import build_mock_registry
from .mcp_server import GatewayMCPServer

# Only phase agents may request capabilities. The orchestrator never does.
REQUESTABLE_AGENTS = {
    # Arcanum internal IDs
    "recon_agent", "web_security_agent", "vulnerability_validator", "reporting_agent",
    "hunt_agent", "validate_agent", "report_agent",
    # Maestro agent IDs (what the live agents actually send)
    "recon-agent", "hunt-agent", "validate-agent", "report-agent",
}


def make_requester(dpm: DynamicPermissionManager,
                   queue: ApprovalQueue,
                   scope_verifier: Any):
    """Build the deterministic DPM request callback used by the MCP server."""

    def request_capability(arguments: dict[str, Any]) -> dict[str, Any]:
        agent_id = str(arguments.get("agent_id", ""))
        if agent_id not in REQUESTABLE_AGENTS:
            return {"decision": "DENY", "reason": f"Agent '{agent_id}' may not request capabilities"}

        target = str(arguments.get("target", ""))
        task_authorized = scope_verifier(target)
        request = PermissionRequest(
            request_id=f"mcp_{uuid.uuid4().hex[:12]}",
            agent_id=agent_id,
            task_id=str(arguments.get("task_id", "")),
            requested_capability=str(arguments.get("capability", "")),
            target=target,
            operation=str(arguments.get("operation", "")),
            justification=str(arguments.get("justification", "")),
            duration_seconds=int(arguments.get("duration_seconds", 300)),
            risk=RiskLevel[str(arguments.get("risk", "LOW")).upper()],
            task_type=str(arguments.get("task_type", "general")),
        )
        result = dpm.request_permission(request, task_authorized=task_authorized)

        response: dict[str, Any] = {
            "decision": result.decision.value,
            "reason": result.reason,
            "policy_ref": result.policy_ref,
            "grant_id": result.grant.grant_id if result.grant else None,
            "approval_id": None,
        }
        if result.decision.value == "REQUIRE_HUMAN_APPROVAL":
            record = queue.get_by_request_id(request.request_id)
            if record:
                response["approval_id"] = record["approval_id"]
        return response

    return request_capability


def build_server(use_real_burp: bool = False,
                 use_real_caido: bool = False,
                 use_harness: bool = True) -> GatewayMCPServer:
    settings = Settings()
    db = Database(settings.db_path)

    def scope_verifier(target: str, operation: str = "") -> bool:
        rows = db.conn.execute("SELECT domain FROM scope_declarations").fetchall()
        domains = {r[0] for r in rows}
        return any(domain in target for domain in domains)

    from ..permissions.permission_policy import PermissionPolicy, PolicyConfig

    queue = ApprovalQueue(settings.approval_db)
    dpm = DynamicPermissionManager(
        store=PermissionStore(settings.base_dir / "arcanum-grants.sqlite"),
        audit=PermissionAuditLog(settings.base_dir / "arcanum-permission-audit.sqlite"),
        policy=PermissionPolicy(PolicyConfig(
            allow_auto_medium=not settings.require_medium_approval
        )),
        scope_verifier=scope_verifier,
        approval_queue=queue,
    )
    gateway = AuthorizationGateway(dpm)
    registry = build_mock_registry(gateway)
    if use_real_burp:
        install_burp_adapter(registry, BurpToolAdapter(settings.burp_mcp_url))
    if use_real_caido:
        install_caido_adapter(registry, CaidoToolAdapter(settings.caido_url))
    if use_harness:
        install_harness_adapter(registry)

    def verify_execution(arguments: dict[str, Any]) -> dict[str, Any]:
        """Head Agent acceptance gate over trusted Maestro runtime evidence."""
        tab_id = str(arguments.get("tab_id", ""))
        task_id = str(arguments.get("task_id", ""))
        execution_id = str(arguments.get("execution_id", ""))
        if not tab_id or not task_id or not execution_id:
            return {"decision": "REJECT", "reason": "tab_id, task_id, execution_id required"}

        try:
            trusted_agent = maestro.resolve_agent(tab_id)
            transcript = maestro.get_transcript(tab_id)
        except Exception as exc:
            return {"decision": "REJECT", "reason": f"Cannot read trusted Maestro records: {exc}"}

        ack = DispatchAcknowledgment(
            task_id=task_id,
            execution_id=execution_id,
            workflow_id=str(arguments.get("workflow_id", "")),
            target_id=str(arguments.get("target_id", "")),
            agent_id=trusted_agent,
            tab_id=tab_id,
            status=DispatchStatus.RESULT_COLLECTED,
        )
        expected_marker = str(arguments.get("expected_marker", ""))
        required_actions = list(arguments.get("required_actions", []))
        criteria = AcceptanceCriteria(
            expected_agent_id=str(arguments.get("expected_agent", "")),
            expected_marker=expected_marker,
            required_actions=required_actions,
            require_tool_execution=bool(arguments.get("require_tool_execution", False)),
        )
        report = ExecutionReport(
            workflow_id=ack.workflow_id,
            target_id=ack.target_id,
            task_id=task_id,
            execution_id=execution_id,
            agent_id=trusted_agent,
            status=ExecutionState.COMPLETED,
            actions_performed=required_actions,
            evidence_refs=[f"maestro:tab:{tab_id}"],
        )
        result = CompletionVerifier().verify(
            report=report,
            ack=ack,
            criteria=criteria,
            transcript=transcript,
            trusted_agent_id=trusted_agent,
        )
        payload = json.loads(result.model_dump_json())
        callback_tab = maestro.find_callback_tab(task_id, execution_id)
        payload["callback_tab"] = callback_tab
        payload["checks"]["callback_correlated"] = bool(callback_tab)
        if not callback_tab:
            payload["decision"] = "REJECT"
            payload["reasons"].append("No correlated Head Agent callback tab found")
        return payload

    return GatewayMCPServer(
        registry,
        permission_requester=make_requester(dpm, queue, scope_verifier),
        execution_verifier=verify_execution,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="arcanum-gateway MCP server")
    parser.add_argument("--real-burp", action="store_true",
                        help="replace mock Burp handlers with the live MCP adapter")
    parser.add_argument("--real-caido", action="store_true",
                        help="replace mock Caido handlers with the live GraphQL adapter")
    parser.add_argument("--no-harness", action="store_true",
                        help="keep finding/evidence handlers as mock handlers")
    args = parser.parse_args()
    build_server(
        use_real_burp=args.real_burp,
        use_real_caido=args.real_caido,
        use_harness=not args.no_harness,
    ).serve_stdin_forever()


if __name__ == "__main__":
    main()

"""High-level runtime API for the Discord/Maestro bridge.

Every objective handled here goes through the deterministic path:
scope → task → skill → DPM grant → AuthorizationGateway → tool result.
Human approvals use the same shared ApprovalQueue as the gateway server.
"""

from __future__ import annotations

from typing import Any

from ..agents import PhaseAgent, SkillRegistry
from ..config.settings import Settings
from ..core.orchestrator import Orchestrator
from ..core.task_manager import TaskManager
from ..memory.database import Database
from ..permissions.approval_queue import ApprovalQueue
from ..permissions.authorization_gateway import AuthorizationGateway
from ..permissions.permission_audit import PermissionAuditLog
from ..permissions.permission_manager import DynamicPermissionManager, PermissionStore
from ..routing import PhaseRouter
from ..security.scope_manager import ScopeManager
from ..tools import (
    BurpToolAdapter,
    CaidoToolAdapter,
    GatewayToolExecutor,
    build_mock_registry,
    install_burp_adapter,
    install_caido_adapter,
    install_harness_adapter,
)


def _stack(settings: Settings | None = None):
    settings = settings or Settings()
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
    install_burp_adapter(registry, BurpToolAdapter(settings.burp_mcp_url))
    install_caido_adapter(registry, CaidoToolAdapter(settings.caido_url))
    install_harness_adapter(registry)
    executor = GatewayToolExecutor(dpm, registry)
    tm = TaskManager(db, ScopeManager(db, settings), settings, dpm=dpm)
    return settings, queue, dpm, gateway, registry, executor, tm


def list_pending(settings: Settings | None = None) -> list[dict[str, Any]]:
    queue = ApprovalQueue((settings or Settings()).approval_db)
    pending = queue.list_pending()
    queue.close()
    return pending


def format_pending(settings: Settings | None = None) -> str:
    pending = list_pending(settings)
    if not pending:
        return "No pending approvals."
    lines = ["Pending approvals:"]
    for item in pending:
        lines.append(
            f"- `{item['approval_id']}` | {item['agent_id']} | "
            f"{item['capability']} on {item['target']} | {item['risk']}"
        )
    return "\n".join(lines)


def approve_pending(approval_id: str, operator: str,
                    settings: Settings | None = None) -> dict[str, Any]:
    _, queue, dpm, gateway, registry, *_ = _stack(settings)
    try:
        record = queue.get(approval_id)
        request = queue.request_from_record(record) if record else None
        result = dpm.approve_pending(approval_id, decided_by=operator)
        response = {
            "decision": result.decision.value,
            "grant_id": result.grant.grant_id if result.grant else None,
            "reason": result.reason,
            "retried": None,
        }
        context = request.phase_context if request is not None else None
        if result.decision.value == "ALLOW" and context and result.grant is not None:
            # Re-run the original phase with the grant that was just approved.
            # Do not request a new grant (that would require approval again).
            ok, tool_result, reason = registry.execute(
                name=context["capability"],
                agent_id=request.agent_id,
                task_id=request.task_id,
                target=request.target,
                operation=request.operation,
                grant_id=result.grant.grant_id,
                params=context.get("params", {}),
            )
            response["retried"] = {
                "task_id": request.task_id,
                "decision": "ALLOW" if ok else "DENY",
                "allowed": ok,
                "reason": reason,
                "grant_id": result.grant.grant_id,
                "result": tool_result,
            }
        return response
    finally:
        dpm.close()
        queue.close()


def deny_pending(approval_id: str, operator: str, reason: str = "",
                 settings: Settings | None = None) -> None:
    _, queue, dpm, *_ = _stack(settings)
    try:
        dpm.deny_pending(approval_id, decided_by=operator, reason=reason)
    finally:
        dpm.close()
        queue.close()


def run_phase(
    objective: str,
    skill_id: str,
    capability: str,
    operation: str,
    target: str,
    operator: str,
    task_type: str = "general",
    params: dict[str, Any] | None = None,
    risk: str = "LOW",
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings, queue, dpm, gateway, registry, executor, tm = _stack(settings)
    try:
        task = tm.create_task(objective, operator, task_type=task_type)
        orch = Orchestrator(tm, dpm=dpm)
        orch.register_agent(PhaseAgent(
            "recon_agent", "recon", executor, SkillRegistry()
        ))
        orch.register_agent(PhaseAgent(
            "web_security_agent", "web-security", executor, SkillRegistry()
        ))
        agent_name = (
            "web_security_agent"
            if capability in {"burp_intercept", "burp_repeater", "http_security_test"}
            else "recon_agent"
        )
        result = orch.dispatch_phase(
            task=task,
            agent_name=agent_name,
            skill_id=skill_id,
            capability=capability,
            operation=operation,
            target=target,
            params=params,
            risk=risk,
            task_authorized=bool(task.scope_refs),
        )
        return {
            "task_id": task.id,
            "status": tm.get_task(task.id).status.value,
            "decision": result.decision,
            "allowed": result.allowed,
            "reason": result.reason,
            "grant_id": result.grant_id,
            "result": result.result,
        }
    finally:
        dpm.close()
        queue.close()


def handle_chat(objective: str, operator: str,
                settings: Settings | None = None) -> dict[str, Any] | None:
    """Route a normal chat objective through deterministic phase selection.

    Returns None when the message has no declared target or no safe phase can
    be selected; the caller should then fall back to ordinary Maestro chat.
    """
    settings = settings or Settings()
    db = Database(settings.db_path)
    domains = ScopeManager(db, settings).extract_domains(objective)
    if not domains:
        return None
    target = domains[0]
    plan = PhaseRouter().select(objective, target)
    if plan is None:
        return None
    return run_phase(
        objective=objective,
        skill_id=plan.skill_id,
        capability=plan.capability,
        operation=plan.operation,
        target=target,
        operator=operator,
        task_type=plan.task_type,
        params=plan.params,
        risk=plan.risk,
        settings=settings,
    )

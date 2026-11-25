"""Head-controlled multi-stage workflow with Maestro result collection.

C2.1.1: one workflow owns one central Lead tab. Each specialist uses one
persistent tab per target, resolved through the TabRegistry. Multiple tasks for
the same target and specialist reuse the same tab; different targets never
share a specialist tab.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from ..config.settings import Settings
from ..core.execution import AcceptanceCriteria
from ..core.task_brief import TaskBrief
from . import maestro
from .communication import respond_to_specialist, detect_message
from .supervisor import run_stage
from .tab_registry import TabRegistry
from ..core.lifecycle import MessageType


SPECIALISTS = ["recon-agent", "hunt-agent", "validate-agent", "report-agent"]


def _stage_brief(
    workflow_id: str, target_id: str, agent: str, objective: str,
    execution_id: str, previous: list[dict[str, Any]], require_fields: dict[str, str],
) -> TaskBrief:
    require_fields = dict(require_fields)
    require_fields["workflow_id"] = workflow_id
    findings = []
    evidence = []
    for item in previous:
        findings.extend(item.get("report", {}).get("findings", []))
        evidence.extend(item.get("report", {}).get("evidence_refs", []))
    return TaskBrief(
        workflow_id=workflow_id,
        target_id=target_id,
        task_id=f"task_{uuid.uuid4().hex[:10]}",
        execution_id=execution_id,
        parent_task_id=previous[-1]["task_id"] if previous else "",
        assigned_agent=agent,
        objective=objective,
        authorized_target=target_id,
        scope_ref=f"scope_{target_id}",
        operation_constraints=["authorized scope only", "reasoning-only control-plane test"],
        available_capabilities=["caido_read", "http_security_test"],
        relevant_findings=findings,
        evidence_refs=evidence,
        expected_activities=["parse task brief", "return structured result"],
        expected_deliverables=["structured execution result"],
        acceptance_criteria=AcceptanceCriteria(
            expected_agent_id=agent,
            expected_workflow_id=workflow_id,
            expected_target_id=target_id,
            required_actions=["parse task brief"],
            required_structured_fields=list(require_fields),
            required_field_values=require_fields,
            min_assistant_messages=1,
        ),
        required_evidence=["task-specific structured fields"],
    )


def _five_agent_objectives(target: str, previous: list[dict[str, Any]]) -> dict[str, str]:
    recon_context = json.dumps(
        [{"task_id": x.get("task_id"), "finding_count": len(x.get("report", {}).get("findings", []))}
         for x in previous],
        ensure_ascii=False,
    )
    return {
        "recon-agent": (
            f"Identify the authorized target and scope for {target} from this brief. "
            "Reasoning task; do not call tools. Return findings: []."
        ),
        "hunt-agent": (
            "Using the accepted recon context " + recon_context + ", rank HIGH/CRITICAL "
            "hypotheses first. Give meaningful LOW/MEDIUM items an escalation assessment, "
            "but do not inflate severity. Reasoning task; do not call tools. Return findings: []."
        ),
        "validate-agent": (
            "Independently apply the proof gate to the accepted context. A finding is "
            "confirmed only with exact request, exact response, and cross-user evidence. "
            "Reasoning task; do not call tools. Return findings: []."
        ),
        "report-agent": (
            "Draft a report from accepted findings and evidence only. Preserve target "
            "and scope references; mark unresolved items clearly. Reasoning task; do not "
            "call tools. Return findings: []."
        ),
    }


def _context(target: str) -> dict[str, str]:
    return {"target_id": target, "authorized_target": target}


def run_five_agent_cycle(
    target: str = "lab.local",
    settings: Settings | None = None,
    registry: TabRegistry | None = None,
    lead_tab_id: str = "",
) -> dict[str, Any]:
    """Run all four specialists under Head Agent verification in order."""
    settings = settings or Settings()
    registry = registry or TabRegistry(settings.tab_registry_path)
    workflow_id = f"wf_{uuid.uuid4().hex[:8]}"
    lead_tab_id = lead_tab_id or maestro.ensure_lead_tab(workflow_id, target, registry)

    previous: list[dict[str, Any]] = []
    stages: dict[str, dict[str, Any]] = {}

    for agent in SPECIALISTS:
        objective = _five_agent_objectives(target, previous)[agent]
        execution_id = f"exec_{uuid.uuid4().hex[:8]}"
        brief = _stage_brief(
            workflow_id, target, agent, objective, execution_id, previous, _context(target),
        )
        result = run_stage(
            brief, settings=settings, timeout=240,
            registry=registry, lead_tab_id=lead_tab_id,
        )
        stages[agent] = result
        if result.get("message_type") == MessageType.CLARIFICATION_REQUEST.value:
            return {
                "decision": "NEEDS_INPUT", "stage": agent, "stages": stages,
                "workflow_id": workflow_id, "lead_tab_id": lead_tab_id,
            }
        if result.get("decision") != "ACCEPT":
            return {
                "decision": "REJECT", "stage": agent, "stages": stages,
                "workflow_id": workflow_id, "lead_tab_id": lead_tab_id,
            }
        previous.append(result)

    final_checks = all(
        stages[a].get("verification", {}).get("decision") == "ACCEPT"
        for a in SPECIALISTS
    )
    return {
        "decision": "ACCEPT" if final_checks else "REJECT",
        "workflow_id": workflow_id,
        "target_id": target,
        "lead_tab_id": lead_tab_id,
        "stages": stages,
        "previous_results": previous,
        "registry": registry.all_for_workflow(workflow_id),
    }


def run_recon_to_hunt(
    target: str = "lab.local",
    settings: Settings | None = None,
    registry: TabRegistry | None = None,
    lead_tab_id: str = "",
) -> dict[str, Any]:
    settings = settings or Settings()
    registry = registry or TabRegistry(settings.tab_registry_path)
    workflow_id = f"wf_{uuid.uuid4().hex[:8]}"
    lead_tab_id = lead_tab_id or maestro.ensure_lead_tab(workflow_id, target, registry)
    previous: list[dict[str, Any]] = []

    recon_eid = f"exec_{uuid.uuid4().hex[:8]}"
    recon_brief = _stage_brief(
        workflow_id, target, "recon-agent",
        "Identify the authorized target and scope from this brief. Reasoning task; do not call tools. Return findings: [].",
        recon_eid, previous, _context(target),
    )
    recon = run_stage(
        recon_brief, settings=settings, timeout=240,
        registry=registry, lead_tab_id=lead_tab_id,
    )
    if recon.get("decision") != "ACCEPT":
        return {
            "decision": "REJECT", "stage": "recon", "result": recon,
            "workflow_id": workflow_id, "lead_tab_id": lead_tab_id,
        }
    previous.append(recon)

    if recon.get("message_type") == MessageType.CLARIFICATION_REQUEST.value:
        return {
            "decision": "NEEDS_INPUT", "stage": "recon", "result": recon,
            "workflow_id": workflow_id, "lead_tab_id": lead_tab_id,
        }

    hunt_eid = f"exec_{uuid.uuid4().hex[:8]}"
    hunt_brief = _stage_brief(
        workflow_id, target, "hunt-agent",
        (
            "Using the accepted recon context, rank the highest-impact authorized "
            "hypotheses. Reasoning task; do not call tools. Return findings: []. "
            "Preserve the accepted target and scope in structured fields."
        ),
        hunt_eid, previous, _context(target),
    )
    hunt = run_stage(
        hunt_brief, settings=settings, timeout=240,
        registry=registry, lead_tab_id=lead_tab_id,
    )
    return {
        "decision": hunt.get("decision", "REJECT"),
        "workflow_id": workflow_id,
        "lead_tab_id": lead_tab_id,
        "recon": recon,
        "hunt": hunt,
        "previous_results": previous,
        "registry": registry.all_for_workflow(workflow_id),
    }


def run_multi_target_cycle(
    targets: list[str],
    agents: list[str] | None = None,
    settings: Settings | None = None,
    registry: TabRegistry | None = None,
    timeout: int = 240,
) -> dict[str, Any]:
    """Run stages across several targets in one workflow.

    Every target gets its own Lead tab and its own specialist tabs, all from
    the same uniform registry key space. Target results stay isolated.
    """
    settings = settings or Settings()
    registry = registry or TabRegistry(settings.tab_registry_path)
    workflow_id = f"wf_multi_{uuid.uuid4().hex[:8]}"
    agents = agents or ["recon-agent"]
    targets = list(dict.fromkeys(targets))  # canonical dedupe within the workflow
    lead_tabs: dict[str, str] = {}

    results: dict[str, dict[str, Any]] = {}
    for target in targets:
        lead_tab_id = maestro.ensure_lead_tab(workflow_id, target, registry)
        lead_tabs[target] = lead_tab_id
        target_results: dict[str, Any] = {}
        previous: list[dict[str, Any]] = []
        for agent in agents:
            execution_id = f"exec_{uuid.uuid4().hex[:8]}"
            brief = _stage_brief(
                workflow_id, target, agent,
                (
                    f"Task for target {target}. Reasoning task; do not call tools. "
                    "Return findings: [] and include the exact target_id and "
                    "authorized_target from this brief."
                ),
                execution_id, previous, _context(target),
            )
            stage = run_stage(
                brief, settings=settings, timeout=timeout,
                registry=registry, lead_tab_id=lead_tab_id,
            )
            target_results[agent] = stage
            if stage.get("decision") != "ACCEPT":
                results[target] = target_results
                return {
                    "decision": "REJECT",
                    "stage": agent,
                    "target": target,
                    "workflow_id": workflow_id,
                    "lead_tab_id": lead_tab_id,
                    "lead_tabs_by_target": lead_tabs,
                    "results": results,
                    "registry": registry.all_for_workflow(workflow_id),
                }
            previous.append(stage)
        results[target] = target_results

    return {
        "decision": "ACCEPT",
        "workflow_id": workflow_id,
        "lead_tab_id": lead_tabs.get(targets[0], "") if targets else "",
        "lead_tabs_by_target": lead_tabs,
        "results": results,
        "registry": registry.all_for_workflow(workflow_id),
    }


def add_target_to_workflow(
    target: str,
    workflow_id: str,
    registry: TabRegistry | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Register a new canonical target in an active workflow.

    Authorization is delegated to the Scope Manager; this function only adds
    the target key to the persistent tab registry. Existing tabs and state are
    untouched, and no specialist tab is created until work is dispatched.
    """
    settings = settings or Settings()
    registry = registry or TabRegistry(settings.tab_registry_path)
    from ..security.scope_manager import ScopeManager
    from ..memory.database import Database

    db = Database(settings.db_path)
    manager = ScopeManager(db, settings)
    scopes = manager.register_targets(f"Add target {target}", "local_operator")
    db.close()
    return {
        "workflow_id": workflow_id,
        "canonical_target": target,
        "scope_refs": [s.scope_ref for s in scopes],
        "specialist_tabs_created": 0,
        "registry": registry.all_for_workflow(workflow_id),
    }

"""Permission models for the Dynamic Permission Manager (DPM).

The DPM is the sole authority for authorization decisions. No agent, orchestrator,
or LLM can grant, expand, or bypass permissions. Profiles define MAXIMUM
capabilities an agent may request — they are NOT active grants.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class PermissionDecision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_HUMAN_APPROVAL = "REQUIRE_HUMAN_APPROVAL"


class PermissionRequest(BaseModel):
    """An agent's request for a capability. A request is NOT authorization."""

    request_id: str
    agent_id: str
    task_id: str
    requested_capability: str
    target: str
    operation: str
    justification: str
    duration_seconds: int = 300
    risk: RiskLevel = RiskLevel.LOW
    scope_ref: str = ""
    task_type: str = "general"
    max_requests: int | None = None
    allowed_methods: list[str] = Field(default_factory=list)
    rate_limit_per_minute: int | None = None
    phase_context: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=_now)
    model_config = {"extra": "forbid"}


class PermissionDecisionResult(BaseModel):
    """The DPM's structured response to a permission request."""

    request_id: str
    decision: PermissionDecision
    reason: str
    policy_ref: str = ""
    grant: "AuthorizationGrant | None" = None
    requires_human_approval: bool = False
    timestamp: datetime = Field(default_factory=_now)
    model_config = {"extra": "forbid"}


class AuthorizationGrant(BaseModel):
    """A temporary, task-specific authorization grant issued by the DPM."""

    grant_id: str
    agent_id: str
    task_id: str
    capability: str
    resource: str
    operations: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevel = RiskLevel.LOW
    created_at: datetime = Field(default_factory=_now)
    expires_at: datetime
    usage_count: int = 0
    usage_limit: int = 1
    last_used_at: datetime | None = None
    status: str = "active"  # active | expired | revoked | used
    policy_ref: str = ""
    scope_ref: str = ""
    task_type: str = "general"
    model_config = {"extra": "forbid"}

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at

    @property
    def is_usable(self) -> bool:
        return self.status == "active" and not self.is_expired and self.usage_count < self.usage_limit


class PermissionProfile(BaseModel):
    """Maximum capabilities an agent may request. NOT an active grant."""

    agent_id: str
    max_capabilities: set[str] = Field(default_factory=set)
    allowed_risk_auto_approve: RiskLevel = RiskLevel.LOW
    description: str = ""
    model_config = {"extra": "forbid"}


class PermissionAuditRecord(BaseModel):
    """Append-only audit record for authorization decisions."""

    record_id: str
    event_type: str
    agent_id: str
    task_id: str
    requested_capability: str
    target: str
    decision: str
    decision_reason: str
    grant_id: str | None = None
    policy_ref: str = ""
    timestamp: datetime = Field(default_factory=_now)
    model_config = {"extra": "forbid"}


# Default permission profiles — maximum capabilities per agent
DEFAULT_PROFILES: dict[str, PermissionProfile] = {
    "master_orchestrator": PermissionProfile(
        agent_id="master_orchestrator",
        max_capabilities={"task_management", "agent_dispatch", "finding_read", "audit_read"},
        allowed_risk_auto_approve=RiskLevel.LOW,
        description="Coordinates only. No protected tool access, no grant creation.",
    ),
    "recon_agent": PermissionProfile(
        agent_id="recon_agent",
        max_capabilities={
            "http_read", "dns_read", "cert_transparency_read", "passive_js_extract",
            "caido_read", "caido_traffic_query",
        },
        allowed_risk_auto_approve=RiskLevel.LOW,
        description="Read-only reconnaissance. Passive Caido traffic only; no Burp interception.",
    ),
    "web_security_agent": PermissionProfile(
        agent_id="web_security_agent",
        max_capabilities={
            "http_security_test", "caido_read", "caido_traffic_query",
            "burp_intercept", "burp_repeater", "finding_write", "evidence_write",
        },
        allowed_risk_auto_approve=RiskLevel.LOW,
        description="Controlled web testing on authorized targets only.",
    ),
    "ai_red_team_agent": PermissionProfile(
        agent_id="ai_red_team_agent",
        max_capabilities={"prompt_injection_test", "mock_tool_invoke", "synthetic_doc_read"},
        allowed_risk_auto_approve=RiskLevel.LOW,
        description="Synthetic/mock targets only by default.",
    ),
    "code_analysis_agent": PermissionProfile(
        agent_id="code_analysis_agent",
        max_capabilities={"source_read", "config_read", "static_analysis"},
        allowed_risk_auto_approve=RiskLevel.LOW,
        description="Read-only source code analysis. No network, no writes.",
    ),
    "vulnerability_validator": PermissionProfile(
        agent_id="vulnerability_validator",
        max_capabilities={"evidence_read", "http_validation", "finding_status_write"},
        allowed_risk_auto_approve=RiskLevel.LOW,
        description="Evidence inspection and approved validation only.",
    ),
    "reporting_agent": PermissionProfile(
        agent_id="reporting_agent",
        max_capabilities={"finding_read", "report_write"},
        allowed_risk_auto_approve=RiskLevel.LOW,
        description="Read-only findings access and report generation.",
    ),
    # Maestro agent IDs — same ceilings as their Arcanum counterparts.
    "hunt-agent": PermissionProfile(
        agent_id="hunt-agent",
        max_capabilities={
            "http_security_test", "caido_read", "caido_traffic_query",
            "burp_intercept", "burp_repeater", "finding_write", "evidence_write",
        },
        allowed_risk_auto_approve=RiskLevel.CRITICAL,
        description="Web Security Agent (Maestro). Hunts all severities.",
    ),
    "recon-agent": PermissionProfile(
        agent_id="recon-agent",
        max_capabilities={
            "http_read", "dns_read", "cert_transparency_read", "passive_js_extract",
            "caido_read", "caido_traffic_query",
        },
        allowed_risk_auto_approve=RiskLevel.LOW,
        description="Reconnaissance Agent (Maestro). Passive only.",
    ),
    "validate-agent": PermissionProfile(
        agent_id="validate-agent",
        max_capabilities={"evidence_read", "http_validation", "finding_status_write"},
        allowed_risk_auto_approve=RiskLevel.CRITICAL,
        description="Validation Agent (Maestro). Proof gate enforced.",
    ),
    "report-agent": PermissionProfile(
        agent_id="report-agent",
        max_capabilities={"finding_read", "report_write"},
        allowed_risk_auto_approve=RiskLevel.LOW,
        description="Reporting Agent (Maestro). Confirmed findings only.",
    ),
}

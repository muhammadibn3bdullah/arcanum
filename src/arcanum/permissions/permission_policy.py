"""Deterministic permission policy engine.

The DPM calls this to evaluate a request. No LLM participates in the decision.
Policy is configuration-driven; the default policy below is conservative.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .permission_models import PermissionDecision, PermissionProfile, PermissionRequest, RiskLevel


PROXY_CAPABILITIES = {
    "burp_intercept", "burp_repeater", "caido_read", "caido_traffic_query"
}
PASSIVE_PROXY_CAPABILITIES = {"caido_read", "caido_traffic_query"}
ACTIVE_PROXY_CAPABILITIES = {"burp_intercept", "burp_repeater"}

# Deterministic task-type -> allowed proxy tool mapping (architecture 8.1).
PROXY_TASK_TYPE_MAP: dict[str, set[str]] = {
    "general": set(PROXY_CAPABILITIES),
    "passive_recon": set(PASSIVE_PROXY_CAPABILITIES),
    "passive_api_fingerprint": set(PASSIVE_PROXY_CAPABILITIES),
    "active_web_test": set(ACTIVE_PROXY_CAPABILITIES),
    "manual_intercept": set(ACTIVE_PROXY_CAPABILITIES),
}


@dataclass
class PolicyConfig:
    """Configuration for the permission decision engine."""

    # All four risk levels are auto-approved after hard checks so Arcanum can
    # hunt and report the full severity range autonomously.
    allow_auto_low: bool = True
    allow_auto_medium: bool = True
    # HIGH/CRITICAL are auto-approved after all hard checks so the framework
    # can hunt the full severity range autonomously. Active proxy tools still
    # require the human step-up enforced above.
    require_human_approval_high: bool = False
    deny_critical: bool = False
    medium_auto_approved_capabilities: set[str] = field(default_factory=set)
    max_grant_duration_seconds: int = 600
    scope_required_for_all: bool = True

    # Grant constraints enforced by the gateway at execution time
    default_max_requests: int = 1
    default_allowed_methods: list[str] = field(default_factory=list)
    default_rate_limit_per_minute: int = 0

    # Capabilities that are always denied regardless of profile/scope
    always_denied: set[str] = field(default_factory=set)


class PermissionPolicy:
    def __init__(self, config: PolicyConfig | None = None):
        self.config = config or PolicyConfig()

    def evaluate(
        self,
        request: PermissionRequest,
        profile: PermissionProfile | None,
        task_authorized: bool,
        scope_verified: bool,
        approved_by_human: bool = False,
        denied_proxy_capabilities: set[str] | None = None,
    ) -> tuple[PermissionDecision, str, str, bool]:
        """Return (decision, reason, policy_ref, requires_human_approval)."""

        policy_ref = "default_policy_v1"

        # 1. Agent must exist
        if profile is None:
            return (
                PermissionDecision.DENY,
                f"Agent '{request.agent_id}' has no registered permission profile",
                "profile_not_found",
                False,
            )

        # 2. Capability must be in the agent's maximum profile
        if request.requested_capability not in profile.max_capabilities:
            return (
                PermissionDecision.DENY,
                f"Capability '{request.requested_capability}' is not in the "
                f"maximum profile for agent '{request.agent_id}'",
                "capability_not_in_profile",
                False,
            )

        # 2b. Proxy tool selection is deterministic: task type decides the tool.
        if request.requested_capability in PROXY_CAPABILITIES:
            allowed_for_task = PROXY_TASK_TYPE_MAP.get(request.task_type, set())
            if request.requested_capability not in allowed_for_task:
                return (
                    PermissionDecision.DENY,
                    f"Proxy capability '{request.requested_capability}' is not allowed "
                    f"for task type '{request.task_type}'",
                    "proxy_tool_not_allowed_for_task_type",
                    False,
                )
            # Invariant 12: no switching to an alternative proxy tool after a denial.
            prior = denied_proxy_capabilities or set()
            if not approved_by_human and any(cap != request.requested_capability for cap in prior):
                return (
                    PermissionDecision.DENY,
                    "Alternative proxy tool after denial is not permitted without human approval",
                    "alternative_proxy_tool_after_denial",
                    False,
                )
            # Active interception always requires a human step-up unless already approved.
            if request.requested_capability in ACTIVE_PROXY_CAPABILITIES and not approved_by_human:
                return (
                    PermissionDecision.REQUIRE_HUMAN_APPROVAL,
                    f"ACTIVE proxy capability '{request.requested_capability}' requires human approval",
                    "active_proxy_requires_human_approval",
                    True,
                )

        # 3. Task must be authorized
        if not task_authorized:
            return (
                PermissionDecision.DENY,
                f"Task '{request.task_id}' is not authorized",
                "task_not_authorized",
                False,
            )

        # 4. Scope must be verified
        if self.config.scope_required_for_all and not scope_verified:
            return (
                PermissionDecision.DENY,
                f"Target '{request.target}' is not within the approved task scope",
                "scope_not_verified",
                False,
            )

        # 5. Always-denied capabilities
        if request.requested_capability in self.config.always_denied:
            return (
                PermissionDecision.DENY,
                f"Capability '{request.requested_capability}' is always denied by policy",
                "always_denied",
                False,
            )

        # 6. Duration check
        if request.duration_seconds > self.config.max_grant_duration_seconds:
            return (
                PermissionDecision.DENY,
                f"Requested duration {request.duration_seconds}s exceeds maximum "
                f"{self.config.max_grant_duration_seconds}s",
                "duration_exceeds_maximum",
                False,
            )

        # 7. Risk-based decision
        risk = request.risk

        # HIGH and CRITICAL are auto-approved so Arcanum can autonomously hunt
        # the full severity range. Every hard check (profile, capability, task,
        # scope, always_denied, duration, and the active-proxy step-up above)
        # has already run before this point.
        if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            return (
                PermissionDecision.ALLOW,
                f"{risk.value} risk operation '{request.operation}' auto-approved "
                f"for agent '{request.agent_id}' on task '{request.task_id}'",
                f"{risk.value.lower()}_auto_approved",
                False,
            )

        if risk == RiskLevel.MEDIUM:
            if request.requested_capability in self.config.medium_auto_approved_capabilities:
                return (
                    PermissionDecision.ALLOW,
                    f"MEDIUM risk auto-approved for capability '{request.requested_capability}'",
                    "medium_policy_approved",
                    False,
                )
            if self.config.allow_auto_medium or approved_by_human:
                return (
                    PermissionDecision.ALLOW,
                    "MEDIUM risk auto-approved by global policy" if not approved_by_human
                    else "MEDIUM risk operation approved by human operator",
                    "medium_auto_approved" if not approved_by_human else "medium_human_approved",
                    False,
                )
            return (
                PermissionDecision.REQUIRE_HUMAN_APPROVAL,
                f"MEDIUM risk operation '{request.operation}' requires human approval",
                "medium_requires_approval",
                True,
            )

        # LOW risk
        if risk == RiskLevel.LOW:
            if self.config.allow_auto_low or approved_by_human:
                return (
                    PermissionDecision.ALLOW,
                    f"LOW risk operation '{request.operation}' auto-approved "
                    f"for agent '{request.agent_id}' on task '{request.task_id}'",
                    "low_auto_approved",
                    False,
                )
            return (
                PermissionDecision.REQUIRE_HUMAN_APPROVAL,
                "LOW risk auto-approval disabled by policy",
                "low_requires_approval",
                True,
            )

        # Fallback
        return (
            PermissionDecision.DENY,
            f"Unknown risk level '{risk}'",
            "unknown_risk",
            False,
        )

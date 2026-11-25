"""Dynamic Permission Manager (DPM) — sole authority for authorization.

The DPM evaluates permission requests deterministically and issues temporary,
task-specific grants. No LLM or agent can grant, expand, or bypass permissions.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from .approval_queue import ApprovalQueue
from .permission_audit import PermissionAuditLog
from .permission_models import (
    AuthorizationGrant,
    DEFAULT_PROFILES,
    PermissionAuditRecord,
    PermissionDecision,
    PermissionDecisionResult,
    PermissionProfile,
    PermissionRequest,
    RiskLevel,
)
from .permission_policy import PROXY_CAPABILITIES, PermissionPolicy, PolicyConfig


class PermissionStore:
    """Dedicated SQLite store for grants (separate from findings/audit)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS grants (
                grant_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                capability TEXT NOT NULL,
                resource TEXT NOT NULL,
                operations TEXT NOT NULL,
                constraints TEXT NOT NULL,
                risk TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                usage_count INTEGER NOT NULL,
                usage_limit INTEGER NOT NULL,
                status TEXT NOT NULL,
                policy_ref TEXT NOT NULL,
                scope_ref TEXT NOT NULL,
                last_used_at TEXT,
                task_type TEXT NOT NULL DEFAULT 'general'
            )
            """
        )
        # Lightweight migration for existing grant DBs.
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(grants)").fetchall()}
        if "last_used_at" not in cols:
            self.conn.execute("ALTER TABLE grants ADD COLUMN last_used_at TEXT")
        if "task_type" not in cols:
            self.conn.execute("ALTER TABLE grants ADD COLUMN task_type TEXT NOT NULL DEFAULT 'general'")
        self.conn.commit()

    def save(self, grant: AuthorizationGrant) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO grants
               (grant_id, agent_id, task_id, capability, resource, operations,
                constraints, risk, created_at, expires_at, usage_count,
                usage_limit, status, policy_ref, scope_ref, last_used_at, task_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                grant.grant_id,
                grant.agent_id,
                grant.task_id,
                grant.capability,
                grant.resource,
                json.dumps(grant.operations),
                json.dumps(grant.constraints),
                grant.risk.value,
                grant.created_at.isoformat(),
                grant.expires_at.isoformat(),
                grant.usage_count,
                grant.usage_limit,
                grant.status,
                grant.policy_ref,
                grant.scope_ref,
                grant.last_used_at.isoformat() if grant.last_used_at else None,
                grant.task_type,
            ),
        )
        self.conn.commit()

    def get(self, grant_id: str) -> AuthorizationGrant | None:
        row = self.conn.execute(
            "SELECT * FROM grants WHERE grant_id = ?", (grant_id,)
        ).fetchone()
        if row is None:
            return None
        return AuthorizationGrant(
            grant_id=row["grant_id"],
            agent_id=row["agent_id"],
            task_id=row["task_id"],
            capability=row["capability"],
            resource=row["resource"],
            operations=json.loads(row["operations"]),
            constraints=json.loads(row["constraints"]),
            risk=row["risk"],
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            usage_count=row["usage_count"],
            usage_limit=row["usage_limit"],
            status=row["status"],
            policy_ref=row["policy_ref"],
            scope_ref=row["scope_ref"],
            last_used_at=(
                datetime.fromisoformat(row["last_used_at"])
                if row["last_used_at"] else None
            ),
            task_type=row["task_type"] or "general",
        )

    def list_for_task(self, task_id: str) -> list[AuthorizationGrant]:
        rows = self.conn.execute(
            "SELECT grant_id FROM grants WHERE task_id = ?", (task_id,)
        ).fetchall()
        out = []
        for r in rows:
            g = self.get(r["grant_id"])
            if g:
                out.append(g)
        return out

    def list_for_agent(self, agent_id: str) -> list[AuthorizationGrant]:
        rows = self.conn.execute(
            "SELECT grant_id FROM grants WHERE agent_id = ?", (agent_id,)
        ).fetchall()
        out = []
        for r in rows:
            g = self.get(r["grant_id"])
            if g:
                out.append(g)
        return out

    def revoke(self, grant_id: str) -> None:
        self.conn.execute(
            "UPDATE grants SET status = 'revoked' WHERE grant_id = ?",
            (grant_id,),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


class DynamicPermissionManager:
    def __init__(
        self,
        store: PermissionStore,
        audit: PermissionAuditLog,
        policy: PermissionPolicy | None = None,
        profiles: dict[str, PermissionProfile] | None = None,
        scope_verifier: Callable[[str], bool] | None = None,
        approval_queue: ApprovalQueue | None = None,
    ):
        self.store = store
        self.audit = audit
        self.approval_queue = approval_queue
        self.policy = policy or PermissionPolicy()
        self.profiles = profiles if profiles is not None else dict(DEFAULT_PROFILES)
        self.scope_verifier = scope_verifier or (lambda _: True)

    def _profile(self, agent_id: str) -> PermissionProfile | None:
        return self.profiles.get(agent_id)

    def _verify_scope(self, target: str, operation: str = "") -> bool:
        """Re-check target/operation against the live scope verifier."""
        try:
            return bool(self.scope_verifier(target, operation))
        except TypeError:
            return bool(self.scope_verifier(target))

    def _prior_proxy_denials(self, agent_id: str, task_id: str) -> set[str]:
        return self.audit.denied_capabilities_for_task(agent_id, task_id, PROXY_CAPABILITIES)

    def _audit(
        self,
        event_type: str,
        request: PermissionRequest,
        decision: PermissionDecision,
        reason: str,
        policy_ref: str,
        grant_id: str | None = None,
    ) -> None:
        self.audit.record(
            PermissionAuditRecord(
                record_id=f"audit_{event_type}_{request.request_id}_{request.agent_id}_{request.task_id}",
                event_type=event_type,
                agent_id=request.agent_id,
                task_id=request.task_id,
                requested_capability=request.requested_capability,
                target=request.target,
                decision=decision.value,
                decision_reason=reason,
                grant_id=grant_id,
                policy_ref=policy_ref,
                timestamp=datetime.now(timezone.utc),
            )
        )

    def request_permission(
        self,
        request: PermissionRequest,
        task_authorized: bool = True,
        scope_verified: bool | None = None,
    ) -> PermissionDecisionResult:
        """Evaluate a permission request. Returns a decision; may issue a grant."""
        if scope_verified is None:
            scope_verified = self._verify_scope(request.target, request.operation)

        profile = self._profile(request.agent_id)
        decision, reason, policy_ref, requires_human = self.policy.evaluate(
            request,
            profile,
            task_authorized,
            scope_verified,
            denied_proxy_capabilities=self._prior_proxy_denials(
                request.agent_id, request.task_id
            ),
        )

        grant = None
        event_type = "permission_denied"
        if decision == PermissionDecision.ALLOW:
            grant = self._issue_grant(request, policy_ref)
            event_type = "permission_granted"
        elif decision == PermissionDecision.REQUIRE_HUMAN_APPROVAL:
            event_type = "permission_requires_approval"

        self._audit(event_type, request, decision, reason, policy_ref,
                    grant.grant_id if grant else None)

        if self.approval_queue is not None:
            self.approval_queue.submit(request, PermissionDecisionResult(
                request_id=request.request_id,
                decision=decision,
                reason=reason,
                policy_ref=policy_ref,
                grant=None,
                requires_human_approval=requires_human,
            ))

        return PermissionDecisionResult(
            request_id=request.request_id,
            decision=decision,
            reason=reason,
            policy_ref=policy_ref,
            grant=grant,
            requires_human_approval=requires_human,
        )

    def approve_request(
        self,
        request: PermissionRequest,
        task_authorized: bool = True,
        scope_verified: bool | None = None,
    ) -> PermissionDecisionResult:
        """Approve a request, but only after ALL hard checks still pass.

        Human approval may satisfy risk-based gates (HIGH/MEDIUM/active proxy),
        but it cannot override profile, task, scope, always-denied, duration,
        or task-type/proxy-tool restrictions.
        """
        if scope_verified is None:
            scope_verified = self._verify_scope(request.target, request.operation)

        profile = self._profile(request.agent_id)
        decision, reason, policy_ref, requires_human = self.policy.evaluate(
            request,
            profile,
            task_authorized,
            scope_verified,
            approved_by_human=True,
        )

        grant = None
        if decision == PermissionDecision.ALLOW:
            grant = self._issue_grant(request, "human_approved")
            self._audit(
                "permission_approved_by_human",
                request,
                decision,
                reason,
                "human_approved",
                grant.grant_id,
            )
        else:
            self._audit("permission_approval_denied", request, decision, reason, policy_ref)

        return PermissionDecisionResult(
            request_id=request.request_id,
            decision=decision,
            reason=reason,
            policy_ref=policy_ref,
            grant=grant,
            requires_human_approval=requires_human,
        )

    def approve_pending(self, approval_id: str, decided_by: str) -> PermissionDecisionResult:
        """Operator approves a queued request. Re-runs every hard check."""
        if self.approval_queue is None:
            raise RuntimeError("No approval queue is configured for this DPM")
        record = self.approval_queue.get(approval_id)
        if record is None or record["status"] != "pending":
            raise ValueError(f"Approval '{approval_id}' is not pending")
        request = self.approval_queue.request_from_record(record)
        result = self.approve_request(request, task_authorized=True)
        self.approval_queue.decide(
            approval_id,
            approved=result.decision == PermissionDecision.ALLOW,
            decided_by=decided_by,
            reason=result.reason,
        )
        return result

    def deny_pending(self, approval_id: str, decided_by: str, reason: str = "") -> None:
        """Operator denies a queued request; no grant is created."""
        if self.approval_queue is None:
            raise RuntimeError("No approval queue is configured for this DPM")
        record = self.approval_queue.get(approval_id)
        if record is None or record["status"] != "pending":
            raise ValueError(f"Approval '{approval_id}' is not pending")
        request = self.approval_queue.request_from_record(record)
        self.approval_queue.decide(approval_id, approved=False,
                                   decided_by=decided_by, reason=reason)
        self._audit("permission_denied_by_human", request, PermissionDecision.DENY,
                    reason or "Denied by human operator", "human_denied")

    def _issue_grant(self, request: PermissionRequest, policy_ref: str) -> AuthorizationGrant:
        now = datetime.now(timezone.utc)
        duration = min(request.duration_seconds, self.policy.config.max_grant_duration_seconds)
        grant = AuthorizationGrant(
            grant_id=f"grant_{uuid.uuid4().hex[:12]}",
            agent_id=request.agent_id,
            task_id=request.task_id,
            capability=request.requested_capability,
            resource=request.target,
            operations=[request.operation],
            constraints={
                "max_requests": (
                    request.max_requests
                    if request.max_requests is not None
                    else self.policy.config.default_max_requests
                ),
                "allowed_methods": (
                    request.allowed_methods
                    or list(self.policy.config.default_allowed_methods)
                ),
                "rate_limit_per_minute": (
                    request.rate_limit_per_minute
                    if request.rate_limit_per_minute is not None
                    else self.policy.config.default_rate_limit_per_minute
                ),
            },
            risk=request.risk,
            created_at=now,
            expires_at=now + timedelta(seconds=duration),
            usage_count=0,
            usage_limit=(
                request.max_requests
                if request.max_requests is not None
                else self.policy.config.default_max_requests
            ),
            status="active",
            policy_ref=policy_ref,
            scope_ref=request.scope_ref,
            task_type=request.task_type,
        )
        self.store.save(grant)
        return grant

    def validate_grant(
        self,
        grant_id: str,
        agent_id: str,
        task_id: str,
        capability: str,
        target: str,
        operation: str,
    ) -> tuple[bool, str, AuthorizationGrant | None]:
        grant = self.store.get(grant_id)
        if grant is None:
            return False, "Grant not found", None
        if grant.agent_id != agent_id:
            return False, "Grant belongs to a different agent", grant
        if grant.task_id != task_id:
            return False, "Grant belongs to a different task", grant
        if grant.capability != capability:
            return False, "Grant does not cover the requested capability", grant
        if grant.resource != target:
            return False, "Grant does not cover the requested resource", grant
        if operation not in grant.operations:
            return False, "Grant does not cover the requested operation", grant
        if grant.usage_count >= grant.usage_limit:
            return False, "Grant usage limit exceeded", grant
        if grant.is_expired:
            grant.status = "expired"
            self.store.save(grant)
            return False, "Grant has expired", grant
        if grant.status != "active":
            return False, f"Grant status is '{grant.status}'", grant
        return True, "Grant is valid", grant

    def verify_scope_for_execution(
        self, grant: AuthorizationGrant, target: str, operation: str
    ) -> tuple[bool, str]:
        """Re-check live scope at execution; revoke the grant if scope changed."""
        if not self._verify_scope(target, operation):
            self.revoke_grant(grant.grant_id, "scope_changed")
            return False, "Target/operation is no longer within the approved task scope"
        return True, "Scope is valid"

    def consume_grant(self, grant_id: str) -> None:
        grant = self.store.get(grant_id)
        if grant is not None:
            grant.usage_count += 1
            grant.last_used_at = datetime.now(timezone.utc)
            if grant.usage_count >= grant.usage_limit:
                grant.status = "used"
            self.store.save(grant)

    def revoke_grant(self, grant_id: str, reason: str = "manual_revocation") -> None:
        grant = self.store.get(grant_id)
        if grant is not None:
            grant.status = "revoked"
            self.store.save(grant)
            self._audit_revoke(grant, reason)

    def _audit_revoke(self, grant: AuthorizationGrant, reason: str) -> None:
        self.audit.record(
            PermissionAuditRecord(
                record_id=f"revoke_{grant.grant_id}",
                event_type="grant_revoked",
                agent_id=grant.agent_id,
                task_id=grant.task_id,
                requested_capability=grant.capability,
                target=grant.resource,
                decision="REVOKED",
                decision_reason=reason,
                grant_id=grant.grant_id,
                policy_ref=grant.policy_ref,
                timestamp=datetime.now(timezone.utc),
            )
        )

    def revoke_for_task(self, task_id: str) -> int:
        grants = self.store.list_for_task(task_id)
        for g in grants:
            if g.status == "active":
                g.status = "revoked"
                self.store.save(g)
        return len(grants)

    def revoke_for_agent(self, agent_id: str) -> int:
        grants = self.store.list_for_agent(agent_id)
        for g in grants:
            if g.status == "active":
                g.status = "revoked"
                self.store.save(g)
        return len(grants)

    def escalate(self, request: PermissionRequest, task_authorized: bool = True,
                 scope_verified: bool | None = None) -> PermissionDecisionResult:
        """Controlled escalation — same evaluation, but logged as escalation."""
        result = self.request_permission(request, task_authorized, scope_verified)
        # Re-audit as escalation
        self.audit.record(
            PermissionAuditRecord(
                record_id=f"escalate_{request.request_id}",
                event_type="escalation_request",
                agent_id=request.agent_id,
                task_id=request.task_id,
                requested_capability=request.requested_capability,
                target=request.target,
                decision=result.decision.value,
                decision_reason=result.reason,
                grant_id=result.grant.grant_id if result.grant else None,
                policy_ref=result.policy_ref,
                timestamp=datetime.now(timezone.utc),
            )
        )
        return result

    def list_grants(self, agent_id: str | None = None) -> list[AuthorizationGrant]:
        if agent_id:
            return self.store.list_for_agent(agent_id)
        # return all
        rows = self.store.conn.execute("SELECT grant_id FROM grants").fetchall()
        out = []
        for r in rows:
            g = self.store.get(r["grant_id"])
            if g:
                out.append(g)
        return out

    def close(self) -> None:
        self.store.close()
        self.audit.close()

"""Scope Manager — deterministic target registration and validation.

Phase 1 enforcement:
- only allowlisted operators may declare targets;
- dynamic mode accepts any domain from such an operator and records it;
- allowlist mode accepts only pre-configured domains;
- every declaration is audited;
- declared domains default to passive-first (no active testing grant).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Iterable

from ..config.settings import Settings
from ..core.models import ScopeRef
from ..memory.database import Database

_DOMAIN_RE = re.compile(
    r"(?<![\w.-])(?:(?:https?://)?(?:www\.)?"
    r"([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+))(?![-\w])",
    re.IGNORECASE,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScopeManager:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings

    def extract_domains(self, objective: str) -> list[str]:
        """Extract normalized domains from operator text."""
        found: list[str] = []
        for match in _DOMAIN_RE.finditer(objective):
            domain = match.group(1).lower().rstrip(".")
            if domain.startswith("www."):
                domain = domain[4:]
            if "." not in domain:
                continue
            if domain not in found:
                found.append(domain)
        return found

    def _authorized_operator(self, operator_id: str) -> bool:
        return operator_id in self.settings.allowed_operators

    def register_targets(self, objective: str, operator_id: str) -> list[ScopeRef]:
        """Register operator-declared domains as authorized targets.

        Raises PermissionError when the operator is not allowlisted or when a
        domain is rejected by allowlist mode. The operator's message itself is
        the authorization declaration and is audited verbatim.
        """
        if not self._authorized_operator(operator_id):
            self.db.audit(
                "scope_declaration_denied",
                operator_id,
                objective[:120],
                "operator_not_allowlisted",
                _now_iso(),
            )
            raise PermissionError(
                f"Operator '{operator_id}' is not allowlisted to declare targets"
            )

        domains = self.extract_domains(objective)
        refs: list[ScopeRef] = []
        for domain in domains:
            if (
                self.settings.dynamic_target_mode == "TARGET_ALLOWLIST_ONLY"
                and domain not in self.settings.target_allowlist
            ):
                self.db.audit(
                    "scope_declaration_denied",
                    operator_id,
                    domain,
                    "domain_not_in_allowlist",
                    _now_iso(),
                )
                raise PermissionError(
                    f"Domain '{domain}' is not in the configured target allowlist"
                )

            scope = ScopeRef(
                scope_ref=f"scope_{uuid.uuid4().hex[:12]}",
                domain=domain,
                mode=self.settings.dynamic_target_mode,
                operator_id=operator_id,
                objective=objective,
                allowed_operations=["passive_recon", "read_only_analysis"],
                active_testing_authorized=False,
                metadata={"source": "operator_declared"},
            )
            self.db.save_scope(scope)
            self.db.audit(
                "scope_declared",
                operator_id,
                domain,
                f"declared via chat objective; scope_ref={scope.scope_ref}",
                scope.declared_at.isoformat(),
            )
            refs.append(scope)
        return refs

    def validate(self, scope_refs: Iterable[str]) -> bool:
        """All referenced scopes must exist and be non-empty."""
        refs = list(scope_refs)
        return bool(refs) and all(self.db.get_scope(r) is not None for r in refs)

    def get(self, scope_ref: str) -> ScopeRef | None:
        return self.db.get_scope(scope_ref)

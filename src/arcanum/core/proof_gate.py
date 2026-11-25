"""Deterministic proof gate for validation and reporting.

An agent may not mark a finding confirmed, or include it in a report, unless the
proof artifact contains an exact request, its response, and cross-user evidence.
This is code, not a prompt instruction.
"""

from __future__ import annotations

from typing import Iterable

from pydantic import BaseModel, Field


class FindingProof(BaseModel):
    finding_id: str
    request_text: str
    response_text: str
    cross_user_evidence: str
    validated_by: str = ""
    model_config = {"extra": "forbid"}


class ProofGate:
    @staticmethod
    def can_confirm(proof: FindingProof) -> bool:
        return bool(
            proof.finding_id
            and proof.request_text.strip()
            and proof.response_text.strip()
            and proof.cross_user_evidence.strip()
        )

    @staticmethod
    def confirmed_only(
        findings: Iterable[dict], confirmed_ids: set[str]
    ) -> list[dict]:
        return [f for f in findings if str(f.get("id")) in confirmed_ids]

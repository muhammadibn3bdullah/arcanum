"""Deterministic task-type -> skill/capability selection.

The router is plain Python. An LLM may suggest a phase, but only this code
chooses which skill and capability an agent may request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..agents import SkillRegistry

PASSIVE_WORDS = re.compile(r"\b(passive|recon|fingerprint|enumerate|caido|traffic)\b", re.I)
ACTIVE_WORDS = re.compile(r"\b(hunt|test|probe|exploit|attack|inject|ssrf|xss)\b", re.I)
VALIDATE_WORDS = re.compile(r"\b(validate|verify|confirm|proof|evidence)\b", re.I)
REPORT_WORDS = re.compile(r"\b(report|write up|summary|submission)\b", re.I)


@dataclass(frozen=True)
class PhasePlan:
    skill_id: str
    capability: str
    operation: str
    task_type: str
    risk: str = "LOW"
    params: dict[str, Any] = field(default_factory=dict)


class PhaseRouter:
    def __init__(self, skill_registry: SkillRegistry | None = None):
        self.skills = skill_registry or SkillRegistry()

    def _first_installed(self, candidates: list[str]) -> str | None:
        for skill_id in candidates:
            if self.skills.has(skill_id):
                return skill_id
        return None

    def infer_task_type(self, objective: str) -> str:
        if VALIDATE_WORDS.search(objective):
            return "validation"
        if REPORT_WORDS.search(objective):
            return "reporting"
        if ACTIVE_WORDS.search(objective):
            return "active_web_test"
        if PASSIVE_WORDS.search(objective):
            return "passive_recon"
        return "general"

    def select(self, objective: str, target: str,
               task_type: str | None = None) -> PhasePlan | None:
        task_type = task_type or self.infer_task_type(objective)

        if task_type == "passive_recon":
            skill = self._first_installed([
                "api-fingerprint-caido", "web2-recon", "hunt-misc"
            ])
            if not skill:
                return None
            return PhasePlan(
                skill_id=skill,
                capability="caido_read",
                operation="read",
                task_type=task_type,
                params={"host": target},
            )

        if task_type in {"active_web_test", "hunt"}:
            skill = self._first_installed([
                "hunt-xss", "hunt-ssrf", "bug-bounty"
            ])
            if not skill:
                return None
            return PhasePlan(
                skill_id=skill,
                capability="http_security_test",
                operation="test",
                task_type=task_type,
                risk="MEDIUM",
                params={"host": target},
            )

        if task_type == "validation":
            skill = self._first_installed(["triage-validation", "evidence-hygiene"])
            if not skill:
                return None
            return PhasePlan(
                skill_id=skill,
                capability="http_validation",
                operation="validate",
                task_type=task_type,
                params={"host": target},
            )

        if task_type == "reporting":
            skill = self._first_installed(["report-writing", "bugcrowd-reporting"])
            if not skill:
                return None
            return PhasePlan(
                skill_id=skill,
                capability="report_write",
                operation="write",
                task_type=task_type,
                params={"host": target},
            )

        return None

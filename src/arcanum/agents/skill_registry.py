"""Registry of installed Arcanum skills.

A PhaseAgent must load a real, installed skill before it may request a
capability. This keeps skill loading as a deterministic precondition rather
than a prompt-level suggestion.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_SKILL_ROOTS = [
    Path.home() / ".config" / "opencode" / "agents" / "specialists",
    Path.home() / "Projects" / "Hackbot-agents" / "skills",
    Path.home() / "Projects" / "hackbots-starter" / "skills",
]


class SkillRegistry:
    def __init__(self, roots: list[Path] | None = None):
        self.roots = roots if roots is not None else DEFAULT_SKILL_ROOTS

    def has(self, skill_id: str) -> bool:
        if not skill_id or "/" in skill_id or ".." in skill_id:
            return False
        for root in self.roots:
            if (root / f"{skill_id}.md").is_file():
                return True
            if (root / skill_id / "SKILL.md").is_file():
                return True
        return False

    def list(self) -> list[str]:
        found: set[str] = set()
        for root in self.roots:
            if not root.is_dir():
                continue
            for path in root.glob("*.md"):
                found.add(path.stem)
            for path in root.glob("*/SKILL.md"):
                found.add(path.parent.name)
        return sorted(found)

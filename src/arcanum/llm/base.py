from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Minimal text-completion interface used by Arcanum phase agents."""

    name: str = "base"

    @abstractmethod
    def complete(self, prompt: str, model: str | None = None,
                 max_tokens: int | None = None) -> str:
        """Return a text completion. No tool execution or grants are involved."""

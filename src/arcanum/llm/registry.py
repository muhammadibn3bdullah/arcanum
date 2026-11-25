"""Provider selection from environment configuration."""

from __future__ import annotations

import os

from .base import LLMProvider
from .ollama import OllamaProvider
from .opencode import OpenCodeProvider


def get_provider(name: str | None = None, **kwargs) -> LLMProvider:
    name = (name or os.environ.get("ARCANUM_LLM_PROVIDER", "opencode")).lower()
    if name == "ollama":
        return OllamaProvider(**kwargs)
    if name == "opencode":
        return OpenCodeProvider(**kwargs)
    raise ValueError(f"Unknown LLM provider '{name}'")

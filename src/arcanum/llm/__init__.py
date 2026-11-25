"""LLM provider layer (Phase 4).

Providers are adapters only. They never grant permissions and never see the
AuthorizationGateway. Tool access remains deterministic in Arcanum code.
"""

from .base import LLMProvider
from .ollama import OllamaProvider
from .opencode import OpenCodeProvider
from .registry import get_provider

__all__ = ["LLMProvider", "OllamaProvider", "OpenCodeProvider", "get_provider"]

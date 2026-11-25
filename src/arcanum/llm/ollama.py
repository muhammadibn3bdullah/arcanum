"""Optional Ollama provider using the local HTTP API."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .base import LLMProvider


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, url: str | None = None, timeout: float = 60.0):
        self.url = (url or os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.timeout = timeout

    def complete(self, prompt: str, model: str | None = None,
                 max_tokens: int | None = None) -> str:
        body = {
            "model": model or os.environ.get("OLLAMA_MODEL", "llama3.1"),
            "prompt": prompt,
            "stream": False,
        }
        request = urllib.request.Request(
            self.url + "/api/generate",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"Ollama request failed: {exc}") from exc
        return str(payload.get("response", ""))

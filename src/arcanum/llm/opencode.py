"""OpenCode CLI provider.

Uses the local `opencode run` command rather than inventing an HTTP contract.
Model IDs are passed as plain arguments; no shell string interpolation occurs.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from .base import LLMProvider


class OpenCodeProvider(LLMProvider):
    name = "opencode"

    def __init__(self, cli: str | None = None, timeout: float = 300.0):
        self.cli = cli or os.environ.get(
            "OPENCODE_CLI", str(os.path.expanduser("~/.opencode/bin/opencode"))
        )
        self.timeout = timeout

    def complete(self, prompt: str, model: str | None = None,
                 max_tokens: int | None = None) -> str:
        if not shutil.which(self.cli) and not os.path.isfile(self.cli):
            raise RuntimeError(f"OpenCode CLI not found: {self.cli}")
        args = [self.cli, "run"]
        if model:
            args += ["--model", model]
        args.append(prompt)
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=self.timeout, check=False
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"OpenCode provider failed (exit {result.returncode}): "
                f"{(result.stderr or '').strip()[:400]}"
            )
        return (result.stdout or "").strip()

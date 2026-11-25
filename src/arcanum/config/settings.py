"""Central configuration for Arcanum Phase 1.

Default mode is DYNAMIC_TARGET_MODE=operator_declared: any domain sent by an
allowlisted operator is registered as an authorized target and gets its own
engagement folder. Set DYNAMIC_TARGET_MODE=TARGET_ALLOWLIST_ONLY to restrict.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass
class Settings:
    base_dir: Path = Path(__file__).resolve().parents[2]
    db_path: Path | None = None
    targets_dir: Path | None = None
    dynamic_target_mode: str = field(
        default_factory=lambda: os.environ.get(
            "DYNAMIC_TARGET_MODE", "operator_declared"
        )
    )
    allowed_operators: list[str] = field(
        default_factory=lambda: _env_list(
            "ARCANUM_ALLOWED_OPERATORS", "local_operator"
        )
    )
    target_allowlist: list[str] = field(
        default_factory=lambda: _env_list("ARCANUM_TARGET_ALLOWLIST")
    )
    max_iterations: int = 5
    token_budget: int = 0
    burp_mcp_url: str = field(
        default_factory=lambda: os.environ.get(
            "ARCANUM_BURP_MCP_URL", "http://127.0.0.1:9876/mcp"
        )
    )
    caido_url: str = field(
        default_factory=lambda: os.environ.get(
            "ARCANUM_CAIDO_URL",
            os.environ.get("CAIDO_URL", "http://127.0.0.1:8080/graphql"),
        )
    )
    approval_db: Path | None = None
    tab_registry_path: Path | None = None
    require_medium_approval: bool = field(
        default_factory=lambda: os.environ.get(
            "ARCANUM_REQUIRE_MEDIUM_APPROVAL", "0"
        ).lower() in ("1", "true", "yes")
    )

    def __post_init__(self) -> None:
        if self.db_path is None:
            self.db_path = Path(os.environ.get("ARCANUM_HOME", str(Path.home() / ".arcanum"))) / "arcanum.db"
        if self.targets_dir is None:
            self.targets_dir = Path.home() / "Targets"
        if self.approval_db is None:
            env = os.environ.get("ARCANUM_APPROVAL_DB")
            self.approval_db = (
                Path(env) if env else Path(os.environ.get("ARCANUM_HOME", str(Path.home() / ".arcanum"))) / "arcanum-approvals.sqlite"
            )
        if self.tab_registry_path is None:
            env = os.environ.get("ARCANUM_TAB_REGISTRY_DB")
            self.tab_registry_path = (
                Path(env) if env else Path(os.environ.get("ARCANUM_HOME", str(Path.home() / ".arcanum"))) / "arcanum-tabs.sqlite"
            )


def load_settings() -> Settings:
    return Settings()

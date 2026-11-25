#!/usr/bin/env python3
"""Apply deterministic OpenCode permission hardening to Arcanum agents.

This does NOT change raw tool availability for the operator's build agent. It
adds per-agent rules for the master orchestrator, the four phase agents, and
every on-demand specialist. The last matching rule wins in OpenCode, so the
broad deny comes first and narrow allows follow it.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

CONFIG = Path.home() / ".config" / "opencode" / "opencode.json"
SPECIALISTS = Path.home() / ".config" / "opencode" / "agents" / "specialists"

MAIN_AGENTS = [
    "hackbot-lead",
    "recon-agent",
    "hunt-agent",
    "validate-agent",
    "report-agent",
]

RULES = [
    # 1. Default deny for every action. Narrow allows below re-enable only
    #    the capabilities an Arcanum agent actually needs.
    {"action": "*", "resource": "*", "effect": "deny"},

    # 2. Read-only inspection of authorized engagement and framework files.
    {"action": "read", "resource": "~/Targets/*", "effect": "allow"},
    {"action": "read", "resource": "~/Projects/arcanum/*", "effect": "allow"},
    {"action": "read", "resource": "~/Projects/Hackbot-agents/*", "effect": "allow"},
    {"action": "glob", "resource": "*", "effect": "allow"},
    {"action": "grep", "resource": "*", "effect": "allow"},

    # 3. Skills are instructions, not authority. Loading them is allowed;
    #    any tool use they request is still checked by the rules here.
    {"action": "skill", "resource": "*", "effect": "allow"},

    # 4. The only executable tools are gateway-wrapped ones. They still
    #    require a DPM-issued grant_id inside the gateway.
    {"action": "arcanum-gateway_*", "resource": "*", "effect": "allow"},
    {"action": "opencode_list_mcp_resources", "resource": "arcanum-gateway", "effect": "allow"},

    # 5. Secrets and environment files are never readable by Arcanum agents.
    {"action": "read", "resource": "*.env", "effect": "deny"},
    {"action": "read", "resource": "*.env.*", "effect": "deny"},
    {"action": "read", "resource": "**/.env", "effect": "deny"},
    {"action": "read", "resource": "**/.env.*", "effect": "deny"},

    # 6. Explicit raw-tool denials (also covered by the wildcard, kept for
    #    clarity and for policy audits).
    {"action": "shell", "resource": "*", "effect": "deny"},
    {"action": "edit", "resource": "*", "effect": "deny"},
    {"action": "webfetch", "resource": "*", "effect": "deny"},
    {"action": "websearch", "resource": "*", "effect": "deny"},
    {"action": "external_directory", "resource": "*", "effect": "deny"},
    {"action": "execute", "resource": "*", "effect": "deny"},
    {"action": "subagent", "resource": "*", "effect": "deny"},
    {"action": "question", "resource": "*", "effect": "deny"},
    {"action": "harness-ingest_*", "resource": "*", "effect": "deny"},
    {"action": "video-md_*", "resource": "*", "effect": "deny"},

    # C2.1.2: raw Maestro dispatches bypass the persistent tab registry and
    # create duplicate specialist tabs. Deny them for every agent; only the
    # controlled wrapper below may talk to Maestro.
    {"action": "shell", "resource": "maestro-cli dispatch *", "effect": "deny"},
    {"action": "shell", "resource": "maestro-cli send *", "effect": "deny"},
    {"action": "shell", "resource": "*--new-tab*", "effect": "deny"},
]


def agent_ids() -> list[str]:
    ids = list(MAIN_AGENTS)
    if SPECIALISTS.is_dir():
        for path in sorted(SPECIALISTS.glob("*.md")):
            name = path.stem
            ids.append(name)
            ids.append(f"specialists/{name}")
    return ids


# The lead needs one narrow shell exception: the registry-aware wrapper. Raw
# `maestro-cli dispatch` / `send` and `--new-tab` are denied above, so an agent
# can no longer create a duplicate specialist tab by itself.
TAB_DISPATCH_WRAPPER = str(
    Path.home() / "Projects" / "arcanum" / "scripts" / "arcanum-tab-dispatch"
)
AGENT_EXTRA_RULES: dict[str, list[dict[str, str]]] = {
    "hackbot-lead": [
        {"action": "shell", "resource": f"{TAB_DISPATCH_WRAPPER} *", "effect": "allow"},
    ],
}


def main() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    backup = CONFIG.with_suffix(CONFIG.suffix + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(CONFIG, backup)
    print(f"backup: {backup}")

    agents = config.setdefault("agents", {})
    for agent_id in agent_ids():
        rules = json.loads(json.dumps(RULES))
        rules.extend(json.loads(json.dumps(AGENT_EXTRA_RULES.get(agent_id, []))))
        agents.setdefault(agent_id, {})["permissions"] = rules

    CONFIG.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"hardened {len(agent_ids())} agent IDs in {CONFIG}")


if __name__ == "__main__":
    main()

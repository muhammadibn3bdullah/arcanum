import importlib.util
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "harden_opencode_permissions.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("harden_opencode_permissions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rules_default_deny_then_narrow_allows():
    module = load_module()
    rules = module.RULES
    assert rules[0] == {"action": "*", "resource": "*", "effect": "deny"}
    assert {"action": "arcanum-gateway_*", "resource": "*", "effect": "allow"} in rules
    assert {"action": "opencode_list_mcp_resources", "resource": "arcanum-gateway", "effect": "allow"} in rules


def test_raw_tool_denials_present():
    module = load_module()
    pairs = {(r["action"], r["resource"]): r["effect"] for r in module.RULES}
    for action in ("shell", "edit", "webfetch", "websearch", "external_directory", "execute"):
        assert pairs[(action, "*")] == "deny"
    assert pairs[("read", "*.env")] == "deny"
    assert pairs[("harness-ingest_*", "*")] == "deny"
    assert pairs[("video-md_*", "*")] == "deny"


def test_main_and_specialist_agents_covered():
    module = load_module()
    ids = set(module.agent_ids())
    for agent in module.MAIN_AGENTS:
        assert agent in ids
    assert "specialists/hunt-xss" in ids
    assert "hunt-xss" in ids

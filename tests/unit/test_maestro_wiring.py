import importlib.util
import json
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "wire_maestro_agents.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("wire_maestro_agents", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_all_maestro_agents_have_instructions():
    module = load_module()
    assert set(module.SYSTEMS) == {
        "hackbot-lead", "recon-agent", "hunt-agent", "validate-agent", "report-agent"
    }
    for name, text in module.SYSTEMS.items():
        assert "arcanum-gateway" in text
        assert "grant_id" in text
        assert "untrusted" in text.lower()


def test_wiring_preserves_permissions(tmp_path, monkeypatch):
    module = load_module()
    config_path = tmp_path / "opencode.json"
    config_path.write_text(json.dumps({
        "agents": {
            "hackbot-lead": {"permissions": [{"action": "*", "resource": "*", "effect": "deny"}]},
            "recon-agent": {"permissions": []},
        }
    }), encoding="utf-8")
    monkeypatch.setattr(module, "CONFIG", config_path)
    monkeypatch.setattr(module, "shutil", __import__("shutil"))

    module.main()
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["agents"]["hackbot-lead"]["permissions"][0]["effect"] == "deny"
    assert "Master Orchestrator" in data["agents"]["hackbot-lead"]["system"]
    assert "Reconnaissance Agent" in data["agents"]["recon-agent"]["system"]

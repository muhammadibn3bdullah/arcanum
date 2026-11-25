import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def test_required_product_paths_exist():
    for path in (
        "src/arcanum",
        "apps/dashboard/server.ts",
        "apps/dashboard/public/app.tsx",
        "integrations/discord/bridge.py",
        "integrations/harness/mcp_ingest.py",
        "workflows/maestro/playbooks/ARCANUM-01.md",
        "skills/arcanum",
        "skills/vendor/claude-bughunter",
        "skills/registry/registry.json",
        "installers/install.sh",
        "installers/install.ps1",
        "README.md",
        "THIRD_PARTY_NOTICES.md",
        ".github/workflows/ci.yml",
    ):
        assert (ROOT / path).exists(), path


def test_five_custom_skills_present():
    custom = {p.name for p in (ROOT / "skills/arcanum").iterdir() if p.is_dir()}
    assert custom == {
        "api-fingerprint-caido", "interceptor", "js_analysis",
        "lazy_chunk_downloader", "say_hello",
    }


def test_registry_matches_inventory():
    registry = json.loads((ROOT / "skills/registry/registry.json").read_text())
    assert registry["skill_count"] == len(registry["skills"]) >= 80
    assert {s["skill_id"] for s in registry["skills"] if s["source"] == "arcanum"} == {
        "api-fingerprint-caido", "interceptor", "js_analysis",
        "lazy_chunk_downloader", "say_hello",
    }
    assert all(s["content_hash"] for s in registry["skills"])
    assert all(s["attribution"] for s in registry["skills"])


def test_no_runtime_data_or_secrets_tracked():
    for pattern in ("*.db", "*.sqlite", "*.db-wal", "*.db-shm", ".env"):
        assert not list(ROOT.rglob(pattern)), pattern


def test_installer_entry_points_are_documented():
    readme = (ROOT / "README.md").read_text()
    assert "install.sh" in readme
    assert "install.ps1" in readme
    assert "arcanum.git" in readme

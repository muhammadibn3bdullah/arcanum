"""Tests for five-skill / 88-skill install verification.

The installer must preserve the exact five Arcanum skills and the 83 vendored
BugHunter skills, verify every skill's per-file and content hashes, and never
silently overwrite a locally modified skill. ``--check`` must remain
completely write-free.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE_PATH = ROOT / "installers" / "modules" / "install_core.py"

# Python 3.12's dataclasses resolves dataclass fields via
# sys.modules[cls.__module__], so exec_module needs the module registered first.
# Use a test-specific name to avoid colliding with other importlib-loaded copies.
_MODULE_NAME = "install_core_for_skill_verification_tests"
_spec = importlib.util.spec_from_file_location(_MODULE_NAME, CORE_PATH)
core = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_MODULE_NAME] = core
_spec.loader.exec_module(core)

FIVE_IDS = {
    "api-fingerprint-caido",
    "interceptor",
    "js_analysis",
    "lazy_chunk_downloader",
    "say_hello",
}


def build_paths(tmp_path: Path) -> "core.Paths":
    home = tmp_path / "home"
    return core.Paths(
        repo=ROOT,
        home=home,
        prefix=home,
        opencode_config=tmp_path / "opencode-config",
        maestro_config=tmp_path / "maestro-config",
        data_dir=home / "data",
    )


def populate_home(home: Path) -> None:
    """Install all registry skills into the namespaced runtime roots."""
    for source, dest in (
        (ROOT / "skills" / "arcanum", home / "skills" / "arcanum"),
        (
            ROOT / "skills" / "vendor" / "claude-bughunter",
            home / "skills" / "vendor" / "claude-bughunter",
        ),
    ):
        shutil.copytree(source, dest, dirs_exist_ok=True)


# --- verify_skill_inventory --------------------------------------------------


def test_verify_skill_inventory_accepts_complete_install(tmp_path):
    home = tmp_path / "home"
    populate_home(home)

    inventory = core.verify_skill_inventory(home, ROOT)

    assert inventory["ok"] is True, inventory
    assert inventory["counts"]["arcanum"] == 5
    assert inventory["counts"]["vendored"] == 83
    assert inventory["counts"]["total"] == 88
    assert inventory["counts"]["installed_arcanum"] == 5
    assert inventory["counts"]["installed_vendored"] == 83
    assert inventory["counts"]["installed_total"] == 88
    assert set(inventory["expected_arcanum_ids"]) == FIVE_IDS
    assert set(inventory["arcanum_ids"]) == FIVE_IDS
    assert len(inventory["vendored_ids"]) == 83
    assert inventory["missing"] == []
    assert inventory["modified"] == []
    assert inventory["unexpected"] == []
    assert inventory["hash_mismatches"] == []
    assert inventory["hash_ok"] is True
    assert inventory["registry_ok"] is True
    assert inventory["agent_routing_ok"] is True


def test_verify_skill_inventory_flags_modified_skill(tmp_path):
    home = tmp_path / "home"
    populate_home(home)
    skill_md = home / "skills" / "arcanum" / "say_hello" / "SKILL.md"
    skill_md.write_text(
        skill_md.read_text(encoding="utf-8") + "\n# LOCAL EDIT - DO NOT OVERWRITE\n",
        encoding="utf-8",
    )

    inventory = core.verify_skill_inventory(home, ROOT)

    assert inventory["ok"] is False
    assert "say_hello" in inventory["modified"]
    assert "SKILL.md" in inventory["modified_files"]["say_hello"]
    assert "say_hello" in inventory["hash_mismatches"]


def test_verify_skill_inventory_flags_missing_skill(tmp_path):
    home = tmp_path / "home"
    populate_home(home)
    shutil.rmtree(home / "skills" / "vendor" / "claude-bughunter" / "web3-audit")

    inventory = core.verify_skill_inventory(home, ROOT)

    assert inventory["ok"] is False
    assert "web3-audit" in inventory["missing"]
    assert inventory["counts"]["installed_vendored"] == 82


# --- step_skills integration -------------------------------------------------


def test_step_skills_installs_all_88_and_reports_verification_rows(tmp_path):
    paths = build_paths(tmp_path)
    reporter = core.Reporter()
    state: dict = {}

    core.step_skills(paths, {}, reporter, check_only=False, state=state)

    assert (paths.home / "skills" / "arcanum" / "say_hello" / "SKILL.md").is_file()
    assert (
        paths.home / "skills" / "vendor" / "claude-bughunter" / "web3-audit" / "SKILL.md"
    ).is_file()
    assert (paths.opencode_config / "skills" / "say_hello" / "SKILL.md").is_file()
    assert core.verify_skill_inventory(paths.home, ROOT)["ok"] is True

    rows = {row.component: row for row in reporter.rows}
    for component in (
        "Five Arcanum skills",
        "Vendored skills",
        "Skill registry",
        "Agent skill routing",
    ):
        assert component in rows
        assert rows[component].status == "Verified", rows[component]


def test_step_skills_preserves_locally_modified_skill(tmp_path):
    paths = build_paths(tmp_path)
    populate_home(paths.home)
    skill_md = paths.home / "skills" / "arcanum" / "say_hello" / "SKILL.md"
    local_text = skill_md.read_text(encoding="utf-8") + "\n# LOCAL EDIT - KEEP ME\n"
    skill_md.write_text(local_text, encoding="utf-8")

    reporter = core.Reporter()
    state: dict = {}
    core.step_skills(paths, {}, reporter, check_only=False, state=state)

    assert skill_md.read_text(encoding="utf-8") == local_text
    rows = {row.component: row for row in reporter.rows}
    assert "Skill conflict" in rows
    assert "say_hello" in rows["Skill conflict"].detail
    assert rows["Five Arcanum skills"].status != "Verified"
    assert state["skills"]["conflicts"] == ["say_hello"]


def test_step_skills_preserves_locally_modified_flat_root_skill(tmp_path):
    """A locally edited skill in the flat OpenCode root survives re-install.

    The flat root (<opencode-config>/skills/<id>) is written with the same
    overlay as the namespaced roots, so protection must be computed per
    destination: a local edit there must be skipped and reported as a conflict
    rather than silently overwritten.
    """
    paths = build_paths(tmp_path)
    core.step_skills(paths, {}, core.Reporter(), check_only=False, state={})

    flat = paths.opencode_config / "skills" / "say_hello" / "SKILL.md"
    assert flat.is_file()
    local_text = flat.read_text(encoding="utf-8") + "\n# LOCAL FLAT EDIT - KEEP ME\n"
    flat.write_text(local_text, encoding="utf-8")

    assert core.skill_dir_matches_registry(
        flat.parent,
        next(
            entry
            for entry in core.load_skill_registry(ROOT)[0]
            if entry["skill_id"] == "say_hello"
        ),
    ) is False

    reporter = core.Reporter()
    state: dict = {}
    core.step_skills(paths, {}, reporter, check_only=False, state=state)

    assert flat.read_text(encoding="utf-8") == local_text
    rows = {row.component: row for row in reporter.rows}
    assert "Skill conflict" in rows
    assert "say_hello" in rows["Skill conflict"].detail
    assert "say_hello" in state["skills"]["conflicts"]


def test_step_skills_check_is_write_free(tmp_path):
    paths = build_paths(tmp_path)
    reporter = core.Reporter()
    state: dict = {}

    core.step_skills(paths, {}, reporter, check_only=True, state=state)

    assert not paths.home.exists()
    assert not paths.opencode_config.exists()
    components = {row.component for row in reporter.rows}
    assert {
        "Five Arcanum skills",
        "Vendored skills",
        "Skill registry",
        "Agent skill routing",
    } <= components

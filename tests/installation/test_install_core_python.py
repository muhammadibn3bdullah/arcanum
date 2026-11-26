"""Tests for wiring the bootstrapped interpreter into install_core.py.

The shell/PowerShell bootstraps pass the interpreter they produced via
``--python`` and ``ARCANUM_BOOTSTRAP_PYTHON``. The engine must accept that
interpreter, record it in the report/state, and keep ``--check`` completely
write-free (in particular it must never execute a tool such as ``opencode``
that creates config/cache/state directories as a side effect).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE_PATH = ROOT / "installers" / "modules" / "install_core.py"

_spec = importlib.util.spec_from_file_location("install_core", CORE_PATH)
core = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(core)


def build_args(tmp_path: Path, *extra: str):
    return core.parse_args(
        [
            "install",
            "--check",
            "--repo",
            str(ROOT),
            "--home",
            str(tmp_path / "arcanum-home"),
            *extra,
        ]
    )


# --- injected interpreter ---------------------------------------------------


def test_choose_python_accepts_injected_path():
    """choose_python takes the interpreter supplied by the bootstrap."""
    assert core.choose_python(sys.executable) == sys.executable


def test_choose_python_rejects_nonexistent_injected_path(tmp_path):
    assert core.choose_python(str(tmp_path / "missing-python")) is None


def test_choose_python_check_only_accepts_running_interpreter():
    """--check accepts sys.executable using only read-only sys.version_info.

    The engine is already running on this interpreter, so its version is known
    to satisfy ``pyproject.toml``; no subprocess is spawned and no path is
    written. A fully ready host must not be reported as Unsupported.
    """
    expected = sys.executable if sys.version_info >= core.MIN_PYTHON else None
    assert core.choose_python(check_only=True) == expected


def test_parse_args_accepts_python_option(tmp_path):
    choice = str(tmp_path / "chosen-python")
    assert core.parse_args(["install", "--python", choice]).python == choice


def test_step_python_records_bootstrap_python_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCANUM_BOOTSTRAP_PYTHON", sys.executable)
    args = build_args(tmp_path)
    paths = core.build_paths(args)
    reporter = core.Reporter()
    state: dict = {}

    core.step_python(paths, {}, reporter, state, check_only=True, skip_tests=True)

    rows = {row.component: row for row in reporter.rows}
    assert "Bootstrap Python" in rows
    assert rows["Bootstrap Python"].status in {"Verified", "Needs manual action"}
    assert state["python"]["interpreter"] == sys.executable


def test_cli_python_overrides_env_interpreter(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCANUM_BOOTSTRAP_PYTHON", str(tmp_path / "env-python"))
    args = core.parse_args(["install", "--python", sys.executable])
    paths = core.build_paths(args)
    reporter = core.Reporter()
    state: dict = {}

    core.step_python(
        paths,
        {},
        reporter,
        state,
        check_only=True,
        skip_tests=True,
        injected_python=args.python,
    )

    assert state["python"]["interpreter"] == sys.executable
    rows = {row.component: row for row in reporter.rows}
    assert "Bootstrap Python" in rows
    assert sys.executable in rows["Bootstrap Python"].detail


# --- --check must be write-free --------------------------------------------


def test_check_reports_manual_action_without_running_any_tool(tmp_path, monkeypatch, capsys):
    """--check must not execute write-producing tools or create files.

    Fake executables on PATH append to a sentinel log whenever they are run.
    Running ``opencode --version`` (as the old engine did) is exactly what
    creates ~/.cache/opencode, ~/.config/opencode and ~/.local/state/opencode,
    so any execution of these tools is a regression.
    """
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    sentinel = tmp_path / "tool-runs.log"
    script = "#!/bin/sh\nprintf '%s\\n' \"$0\" >> \"$SENTINEL_DIR/tool-runs.log\"\nexit 0\n"
    for name in (
        "python3.15",
        "python3.14",
        "python3.13",
        "python3.12",
        "python3",
        "python",
        "opencode",
        "bun",
        "maestro",
        "maestro-cli",
    ):
        target = fake_bin / name
        target.write_text(script, encoding="utf-8")
        target.chmod(0o755)

    home = tmp_path / "home"
    arc_home = tmp_path / "arcanum-home"
    xdg_config = tmp_path / "xdg-config"
    xdg_cache = tmp_path / "xdg-cache"
    xdg_state = tmp_path / "xdg-state"

    monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ARCANUM_HOME", str(arc_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache))
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg_state))
    monkeypatch.setenv("SENTINEL_DIR", str(tmp_path))
    monkeypatch.delenv("ARCANUM_BOOTSTRAP_PYTHON", raising=False)
    monkeypatch.delenv("ARCANUM_PYTHON", raising=False)

    args = build_args(tmp_path)
    paths = core.build_paths(args)
    reporter = core.Reporter()
    manifest = core.load_manifest(paths.repo, reporter)
    result = core.cmd_install(paths, manifest, reporter, args)
    capsys.readouterr()

    assert result in (0, 2)
    assert any(
        row.status in {"Unsupported", "Needs manual action"}
        for row in reporter.rows
    ), reporter.as_dict()
    assert not sentinel.exists(), "a tool was executed during --check"
    assert not arc_home.exists(), "--check created ARCANUM_HOME"
    assert not home.exists(), "--check wrote under HOME"
    assert not xdg_config.exists(), "--check wrote to the config dir"
    assert not xdg_cache.exists(), "--check wrote to the cache dir"
    assert not xdg_state.exists(), "--check wrote to the state dir"


def test_check_reports_python_verified_and_succeeds_write_free(tmp_path, monkeypatch, capsys):
    """A ready host exits 0 with 'Python: Verified' and no filesystem writes."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    sentinel = tmp_path / "tool-runs.log"
    script = "#!/bin/sh\nprintf '%s\\n' \"$0\" >> \"$SENTINEL_DIR/tool-runs.log\"\nexit 0\n"
    for name in ("opencode", "bun", "maestro", "maestro-cli"):
        target = fake_bin / name
        target.write_text(script, encoding="utf-8")
        target.chmod(0o755)

    home = tmp_path / "home"
    arc_home = tmp_path / "arcanum-home"
    xdg_config = tmp_path / "xdg-config"
    xdg_cache = tmp_path / "xdg-cache"
    xdg_state = tmp_path / "xdg-state"
    monkeypatch.setenv("PATH", str(fake_bin))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ARCANUM_HOME", str(arc_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache))
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg_state))
    monkeypatch.setenv("SENTINEL_DIR", str(tmp_path))
    monkeypatch.delenv("ARCANUM_BOOTSTRAP_PYTHON", raising=False)
    monkeypatch.delenv("ARCANUM_PYTHON", raising=False)

    args = build_args(tmp_path)
    paths = core.build_paths(args)
    reporter = core.Reporter()
    manifest = core.load_manifest(paths.repo, reporter)
    result = core.cmd_install(paths, manifest, reporter, args)
    capsys.readouterr()

    rows = {row.component: row for row in reporter.rows}
    assert rows["Python"].status == "Verified", rows["Python"]
    assert not reporter.bad(), reporter.as_dict()
    assert result == 0
    # PATH-installed tools are detected statically, never executed.
    for name in ("bun", "opencode"):
        assert str(fake_bin / name) in rows[name].detail, rows[name]
    assert not sentinel.exists(), "a tool was executed during --check"
    assert not arc_home.exists(), "--check created ARCANUM_HOME"
    assert not home.exists(), "--check wrote under HOME"
    assert not xdg_config.exists(), "--check wrote to the config dir"
    assert not xdg_cache.exists(), "--check wrote to the cache dir"
    assert not xdg_state.exists(), "--check wrote to the state dir"


def test_step_tool_check_reports_path_installed_tool_without_executing(tmp_path, monkeypatch):
    """step_tool uses shutil.which() in check mode and never runs --version."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    sentinel = tmp_path / "tool-runs.log"
    tool = fake_bin / "bun"
    tool.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$0\" >> \"$SENTINEL_DIR/tool-runs.log\"\nexit 0\n",
        encoding="utf-8",
    )
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))
    monkeypatch.setenv("SENTINEL_DIR", str(tmp_path))

    args = build_args(tmp_path)
    paths = core.build_paths(args)
    reporter = core.Reporter()
    section = {
        "version": "1.4.5",
        "assets": {core.asset_key(): {"url": "https://example.invalid/bun.zip"}},
    }
    found = core.step_tool(
        "bun", section, paths, reporter,
        check_only=True, skip_downloads=True, timeout=5,
    )

    assert found is None  # --check never returns an executable path
    row = reporter.rows[-1]
    assert row.component == "bun"
    assert str(tool) in row.detail, row
    assert row.status == "Needs manual action", row
    assert not sentinel.exists(), "step_tool executed a PATH tool during --check"

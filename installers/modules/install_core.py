#!/usr/bin/env python3
"""Arcanum standalone installer / uninstaller engine.

Thin shell/PowerShell wrappers invoke this module. It is standard-library only
so it can run with ``python3`` from PATH even before a virtualenv exists.

Design rules (enforced here, not just documented):
  * Runtime data goes to ARCANUM_HOME (default ``~/.arcanum``), never to the
    repository. A copy of the Python package is placed at ``<home>/src`` so
    ``Settings.base_dir`` resolves inside the runtime home.
  * Every download is a pinned official release asset with a published SHA-256.
    No remote script is piped into a shell. No admin rights are requested.
  * Every write is idempotent. Existing files are backed up before change;
    existing databases and Discord secrets are never overwritten.
  * Status is reported as a matrix with explicit Unsupported / Failed states.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

IS_WINDOWS = os.name == "nt"
EXE = ".exe" if IS_WINDOWS else ""
SH_SUFFIX = ".cmd" if IS_WINDOWS else ""
ENGINE_VERSION = "1.0.0"
STAMP_TIME = "%Y%m%d-%H%M%S"
CHUNK = 1024 * 1024
MANAGED_MARKER = "# Managed by the Arcanum installer"
PLAYBOOK_MARKER = ".arcanum-installed.json"
BOOTSTRAP_PYTHON_ENV = "ARCANUM_BOOTSTRAP_PYTHON"
MIN_PYTHON = (3, 12)

AGENTS = [
    "hackbot-lead",
    "recon-agent",
    "hunt-agent",
    "validate-agent",
    "report-agent",
]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

STATUSES = [
    "Installed",
    "Verified",
    "Needs authentication",
    "Needs manual action",
    "Unsupported",
    "Failed",
    "Already present",
    "Skipped",
]


@dataclass
class Row:
    component: str
    status: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"component": self.component, "status": self.status, "detail": self.detail}


class Reporter:
    def __init__(self) -> None:
        self.rows: list[Row] = []

    def add(self, component: str, status: str, detail: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        self.rows.append(Row(component, status, detail))
        print(f"[{status:>20}] {component}: {detail}", flush=True)

    def bad(self) -> bool:
        return any(r.status in {"Failed", "Unsupported"} for r in self.rows)

    def print_matrix(self, title: str = "Arcanum status matrix") -> None:
        print(f"\n{title}")
        print("=" * len(title))
        w0 = max([len("Component")] + [len(r.component) for r in self.rows])
        w1 = max([len("Status")] + [len(r.status) for r in self.rows])
        print(f"{'Component':<{w0}}  {'Status':<{w1}}  Detail")
        print(f"{'-' * w0}  {'-' * w1}  {'-' * 40}")
        for row in self.rows:
            print(f"{row.component:<{w0}}  {row.status:<{w1}}  {row.detail}")
        print()

    def as_dict(self) -> list[dict[str, str]]:
        return [r.as_dict() for r in self.rows]


def log(msg: str) -> None:
    print(f"  -> {msg}", flush=True)


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def stamp() -> str:
    return time.strftime(STAMP_TIME)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def normalized_arch() -> str:
    machine = platform.machine().strip().lower()
    if machine in {"x86_64", "amd64", "x64"}:
        return "x86_64"
    if machine in {"aarch64", "arm64"}:
        return "arm64"
    return machine


def platform_key() -> str:
    system = platform.system()
    arch = normalized_arch()
    if system not in {"Darwin", "Linux", "Windows"}:
        return f"{system.lower()}-{arch}"
    return f"{system.lower()}-{arch}"


def asset_key() -> str:
    """Manifest key for the current OS/arch."""
    system = platform.system()
    arch = normalized_arch()
    arch_key = "x64" if arch == "x86_64" else arch
    return f"{system.lower()}-{arch_key}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    candidate = path.with_name(f"{path.name}.bak-{stamp()}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak-{stamp()}-{counter}")
        counter += 1
    shutil.copy2(path, candidate)
    log(f"backup: {candidate}")
    return candidate


def write_text(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        if mode is not None and not IS_WINDOWS:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_json(path: Path, data: Any) -> None:
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def file_identical(src: Path, dst: Path) -> bool:
    if not dst.exists() or src.stat().st_size != dst.stat().st_size:
        return False
    return sha256_file(src) == sha256_file(dst)


def install_file(src: Path, dst: Path, backups: list[str]) -> bool:
    """Copy a file. Returns True if a write happened."""
    if file_identical(src, dst):
        return False
    if dst.exists():
        backup = backup_file(dst)
        if backup:
            backups.append(str(backup))
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def copy_tree(src: Path, dst: Path, ignore: set[str] | None = None) -> int:
    """Overlay a source tree into dst. Never deletes existing files."""
    if not src.is_dir():
        return 0
    ignored = ignore or set()
    count = 0

    def filer(directory: str, names: list[str]) -> set[str]:
        skip = {n for n in names if n in ignored or n.startswith("__pycache__")}
        nonlocal count
        count += len(names) - len(skip)
        return skip

    shutil.copytree(src, dst, dirs_exist_ok=True, ignore=filer)
    return count


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture: bool = True,
    timeout: int | None = None,
    interactive: bool = False,
) -> subprocess.CompletedProcess[str] | None:
    display = " ".join(shlex.quote(str(c)) for c in cmd)
    log(f"$ {display}")
    # Windows batch wrappers need an explicit command interpreter.
    if IS_WINDOWS and cmd and str(cmd[0]).lower().endswith((".cmd", ".bat")):
        cmd = ["cmd", "/c", str(cmd[0]), *[str(c) for c in cmd[1:]]]
    kwargs: dict[str, Any] = {"cwd": str(cwd) if cwd else None, "env": env}
    if interactive:
        return subprocess.run([str(c) for c in cmd], **kwargs)
    kwargs.update({"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "text": True})
    try:
        return subprocess.run([str(c) for c in cmd], timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"command timed out: {display}") from exc


def command_output(cmd: list[str], *, timeout: int = 30) -> str:
    try:
        result = run(cmd, capture=True, timeout=timeout)
    except (OSError, RuntimeError):
        return ""
    if result is None or result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def version_of(executable: Path, *, timeout: int = 30) -> str:
    for flag in ("--version", "version", "-v"):
        out = command_output([executable, flag], timeout=timeout)
        match = re.search(r"(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)", out)
        if match:
            return match.group(1)
    return ""


def parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def nearest_existing(path: Path) -> Path:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return probe


def disk_free_gb(path: Path) -> float:
    target = path if path.exists() else path.parent
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        return shutil.disk_usage(target).free / (1024 ** 3)
    except OSError:
        return -1.0


def http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                return False
            body = response.read(4096)
        payload = json.loads(body.decode("utf-8", errors="replace"))
        return bool(payload.get("ok"))
    except (OSError, ValueError, urllib.error.URLError):
        return False


# ---------------------------------------------------------------------------
# Paths / manifest / platform matrix
# ---------------------------------------------------------------------------

@dataclass
class Paths:
    repo: Path
    home: Path
    prefix: Path
    opencode_config: Path
    maestro_config: Path
    data_dir: Path

    @property
    def bin(self) -> Path:
        return self.prefix / "bin"

    @property
    def tools(self) -> Path:
        return self.prefix / "tools"

    @property
    def venv(self) -> Path:
        return self.home / "venv"

    @property
    def venv_python(self) -> Path:
        name = "python.exe" if IS_WINDOWS else "python"
        return self.venv / ("Scripts" if IS_WINDOWS else "bin") / name

    @property
    def cache(self) -> Path:
        return self.home / "cache" / "downloads"

    @property
    def logs(self) -> Path:
        return self.home / "logs"

    @property
    def state_file(self) -> Path:
        return self.home / "install-state.json"

    @property
    def report_file(self) -> Path:
        return self.home / "install-report.json"


def default_config_dir(app: str) -> Path:
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / app
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / app


def load_manifest(repo: Path, reporter: Reporter) -> dict[str, Any]:
    path = repo / "config" / "dependencies.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        reporter.add("Manifest", "Failed", f"cannot read {path}: {exc}")
        return {}
    if manifest.get("schema_version") != 1:
        reporter.add("Manifest", "Failed", f"unsupported schema_version in {path}")
        return {}
    reporter.add("Manifest", "Verified", f"{path.relative_to(repo)} (schema 1)")
    return manifest


def supported_platform(manifest: dict[str, Any], reporter: Reporter) -> str:
    key = platform_key()
    asset = asset_key()
    required = all(
        isinstance(manifest.get(section, {}).get("assets"), dict)
        for section in ("bun", "opencode")
    )
    opencode_ok = asset in manifest.get("opencode", {}).get("assets", {})
    bun_ok = asset in manifest.get("bun", {}).get("assets", {})
    maestro_assets = manifest.get("maestro", {}).get("assets", {})
    maestro_ok = asset in maestro_assets
    unsupported = [
        entry for entry in manifest.get("maestro", {}).get("unsupported", [])
        if entry.get("platform") == asset
    ]
    reporter.add(
        "Platform",
        "Verified" if (opencode_ok and bun_ok) else "Unsupported",
        f"{key} -> manifest key '{asset}' (bun={bun_ok}, opencode={opencode_ok}, maestro={maestro_ok})",
    )
    if maestro_ok and not unsupported:
        reporter.add("Maestro platform", "Verified", f"official asset available for {asset}")
    elif unsupported:
        reporter.add("Maestro platform", "Unsupported", unsupported[0].get("reason", "no official asset"))
    else:
        reporter.add("Maestro platform", "Unsupported", f"no official v1.1.1-RC asset for {asset}")
    if not required:
        reporter.add("Manifest assets", "Failed", "manifest is missing required asset tables")
    return key


def check_environment(paths: Paths, reporter: Reporter) -> bool:
    free = disk_free_gb(paths.home)
    if free >= 0:
        status = "Verified" if free >= 4 else "Needs manual action"
        reporter.add(
            "Disk space",
            status,
            f"{free:.1f} GiB free at {paths.home} (4 GiB recommended for Maestro assets)",
        )
    else:
        reporter.add("Disk space", "Needs manual action", f"cannot determine free space at {paths.home}")

    targets: list[tuple[str, Path]] = [
        ("ARCANUM_HOME", paths.home),
        ("Install prefix", paths.prefix),
        ("OpenCode config", paths.opencode_config),
        ("Maestro config", paths.maestro_config),
    ]
    writable = True
    for label, target in targets:
        probe = nearest_existing(target)
        ok = probe.exists() and os.access(str(probe), os.W_OK)
        if not ok:
            writable = False
        reporter.add(
            f"Permissions: {label}",
            "Verified" if ok else "Failed",
            f"writable: {probe}" if ok else f"not writable: {probe}",
        )
    if not paths.repo.joinpath("pyproject.toml").exists():
        reporter.add("Repository", "Failed", f"pyproject.toml not found under {paths.repo}")
        return False
    reporter.add("Repository", "Verified", str(paths.repo))
    return writable


def existing_install(paths: Paths, reporter: Reporter) -> dict[str, Any]:
    state: dict[str, Any] = {}
    if paths.state_file.exists():
        try:
            state = json.loads(paths.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            reporter.add("Existing install", "Needs manual action", "install-state.json is unreadable")
    parts = []
    if state:
        parts.append(f"state since {state.get('installed_at', 'unknown')}")
    if paths.venv_python.exists():
        parts.append("venv")
    if (paths.bin / f"maestro-cli{SH_SUFFIX}").exists():
        parts.append("wrappers")
    if paths.home.joinpath("arcanum.db").exists():
        parts.append("databases")
    reporter.add(
        "Existing install",
        "Already present" if parts else "Verified",
        ", ".join(parts) if parts else "fresh installation",
    )
    return state


# ---------------------------------------------------------------------------
# Downloads and safe extraction
# ---------------------------------------------------------------------------

def download_asset(
    asset: dict[str, Any],
    cache: Path,
    reporter: Reporter,
    component: str,
    *,
    check_only: bool = False,
    timeout: int = 900,
) -> Path | None:
    name = asset.get("asset") or ""
    url = asset.get("url") or ""
    expected = (asset.get("sha256") or "").lower()
    if not name or not url:
        reporter.add(component, "Failed", "manifest asset is missing url/asset name")
        return None
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / name
    if target.exists() and expected and sha256_file(target) == expected:
        reporter.add(component, "Verified", f"cached asset {name} matches published SHA-256")
        return target
    if check_only:
        reporter.add(component, "Needs manual action", f"not downloaded: {name}")
        return None
    reporter.add(component, "Installed", f"downloading official asset {name}")
    partial = target.with_name(target.name + ".part")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": f"arcanum-installer/{ENGINE_VERSION}"})
        with urllib.request.urlopen(request, timeout=timeout) as response, partial.open("wb") as handle:
            total = int(response.headers.get("Content-Length") or 0)
            written = 0
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
                if total and written % (25 * CHUNK) < CHUNK:
                    log(f"{written / (1024 ** 2):.0f}/{total / (1024 ** 2):.0f} MiB")
    except (OSError, urllib.error.URLError) as exc:
        if partial.exists():
            partial.unlink()
        reporter.add(component, "Failed", f"download failed ({exc}): {url}")
        return None
    actual = sha256_file(partial)
    if expected and actual != expected:
        partial.unlink()
        reporter.add(
            component,
            "Failed",
            f"SHA-256 mismatch for {name}: expected {expected}, got {actual}. Asset discarded.",
        )
        return None
    if not expected:
        reporter.add(
            component,
            "Needs manual action",
            f"no published checksum; verify manually: shasum -a 256 {target} against the release page {url}",
        )
    os.replace(partial, target)
    return target


def _safe_member(name: str) -> bool:
    path = Path(name)
    return not path.is_absolute() and ".." not in path.parts


def extract_zip(archive: Path, dest: Path) -> None:
    staging = dest.parent / f".{dest.name}.extract-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                if not _safe_member(member.filename):
                    raise RuntimeError(f"unsafe path in archive: {member.filename}")
                if member.is_dir():
                    continue
                target = staging / member.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink, CHUNK)
                mode = member.external_attr >> 16
                if mode and not IS_WINDOWS:
                    with contextlib_suppress():
                        target.chmod(stat.S_IMODE(mode))
        if not dest.exists():
            os.replace(staging, dest)
        else:
            copy_tree(staging, dest)
            shutil.rmtree(staging)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


class contextlib_suppress:
    """Tiny context manager kept local to avoid importing contextlib late."""

    def __enter__(self) -> "contextlib_suppress":
        return self

    def __exit__(self, *args: Any) -> bool:
        return True


def extract_tar(archive: Path, dest: Path) -> None:
    staging = dest.parent / f".{dest.name}.extract-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        with tarfile.open(archive, "r:gz") as tf:
            for member in tf.getmembers():
                if not _safe_member(member.name) or not (member.isfile() or member.isdir()):
                    raise RuntimeError(f"unsafe/unsupported tar member: {member.name}")
                tf.extract(member, path=staging, filter="data") if sys.version_info >= (3, 12) else tf.extract(member, path=staging)
        if not dest.exists():
            os.replace(staging, dest)
        else:
            copy_tree(staging, dest)
            shutil.rmtree(staging)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def extract_deb(archive: Path, dest: Path) -> bool:
    if not shutil.which("dpkg-deb"):
        return False
    staging = dest.parent / f".{dest.name}.extract-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        result = run(["dpkg-deb", "-x", str(archive), str(staging)], timeout=600)
        if result is None or result.returncode != 0:
            raise RuntimeError((result.stdout if result else "")[-500:])
        if not dest.exists():
            os.replace(staging, dest)
        else:
            copy_tree(staging, dest)
            shutil.rmtree(staging)
        return True
    except (OSError, RuntimeError):
        if staging.exists():
            shutil.rmtree(staging)
        return False


def extract_appimage(archive: Path, dest: Path) -> bool:
    staging = dest.parent / f".{dest.name}.extract-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        executable = archive
        if not os.access(str(executable), os.X_OK):
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        result = run([str(executable), "--appimage-extract"], cwd=staging, timeout=900)
        if result is None or result.returncode != 0:
            raise RuntimeError((result.stdout if result else "")[-500:])
        if not dest.exists():
            os.replace(staging, dest)
        else:
            copy_tree(staging, dest)
            shutil.rmtree(staging)
        return True
    except (OSError, RuntimeError):
        if staging.exists():
            shutil.rmtree(staging)
        return False


def find_file(root: Path, names: Iterable[str]) -> Path | None:
    wanted = set(names)
    matches = [p for p in root.rglob("*") if p.is_file() and p.name in wanted]
    if not matches:
        return None
    return sorted(matches, key=lambda p: (len(p.relative_to(root).parts), str(p)))[0]


# ---------------------------------------------------------------------------
# Wrappers
# ---------------------------------------------------------------------------

def posix_wrapper(path: Path, body: list[str], backups: list[str]) -> None:
    content = f"#!/bin/sh\n{MANAGED_MARKER}\n" + "\n".join(body) + "\n"
    if path.exists():
        backup = backup_file(path)
        if backup:
            backups.append(str(backup))
    write_text(path, content, mode=0o755)


def windows_wrappers(path_stem: Path, body_cmd: list[str], body_ps: list[str], backups: list[str]) -> None:
    cmd_path = path_stem.with_suffix(".cmd")
    ps_path = path_stem.with_suffix(".ps1")
    cmd_content = f"@echo off\r\nREM {MANAGED_MARKER}\r\n" + "\r\n".join(body_cmd) + "\r\n"
    ps_content = f"# {MANAGED_MARKER}\r\n" + "\r\n".join(body_ps) + "\r\n"
    for target, content in ((cmd_path, cmd_content), (ps_path, ps_content)):
        if target.exists():
            backup = backup_file(target)
            if backup:
                backups.append(str(backup))
        write_text(target, content)


def q(value: Path | str) -> str:
    return shlex.quote(str(value))


def simple_wrapper(path_stem: Path, target: Path, backups: list[str]) -> Path:
    """Create a platform-correct launcher for a native binary."""
    if IS_WINDOWS:
        windows_wrappers(
            path_stem,
            ["@echo off", f'"{target}" %*'],
            [f"& '{target}' @args", "exit $LASTEXITCODE"],
            backups,
        )
        return path_stem.with_suffix(".cmd")
    posix_wrapper(path_stem, [f"exec {q(target)} \"$@\""], backups)
    return path_stem


def maestro_wrapper(path_stem: Path, electron: Path, cli_js: Path, backups: list[str]) -> Path:
    if IS_WINDOWS:
        windows_wrappers(
            path_stem,
            ["@echo off", "set ELECTRON_RUN_AS_NODE=1", f'"{electron}" "{cli_js}" %*'],
            [
                '$env:ELECTRON_RUN_AS_NODE = "1"',
                f"& '{electron}' '{cli_js}' @args",
                "exit $LASTEXITCODE",
            ],
            backups,
        )
        return path_stem.with_suffix(".cmd")
    posix_wrapper(
        path_stem,
        [
            "ELECTRON_RUN_AS_NODE=1",
            "export ELECTRON_RUN_AS_NODE",
            f"exec {q(electron)} {q(cli_js)} \"$@\"",
        ],
        backups,
    )
    return path_stem


def python_script_wrapper(
    path_stem: Path,
    python: Path,
    script: Path,
    env: dict[str, Path],
    backups: list[str],
) -> Path:
    if IS_WINDOWS:
        lines_cmd = ["@echo off"]
        lines_cmd += [f'set {key}={value}' for key, value in env.items()]
        lines_cmd.append(f'"{python}" "{script}" %*')
        lines_ps = [f"$env:{key} = '{value}'" for key, value in env.items()]
        lines_ps += [f"& '{python}' '{script}' @args", "exit $LASTEXITCODE"]
        windows_wrappers(path_stem, lines_cmd, lines_ps, backups)
        return path_stem.with_suffix(".cmd")
    body = [f'export {key}={q(value)}' for key, value in env.items()]
    body.append(f'exec {q(python)} {q(script)} "$@"')
    posix_wrapper(path_stem, body, backups)
    return path_stem


# ---------------------------------------------------------------------------
# Installation steps
# ---------------------------------------------------------------------------

def interpreter_command(interpreter: str) -> list[str]:
    """Return argv that invokes ``interpreter``.

    A bootstrapped interpreter is an absolute path (which may contain spaces),
    so it must be kept as one argument. A launcher such as ``py -3.12`` is a
    command with arguments and is split accordingly.
    """
    if Path(interpreter).exists() or os.path.isabs(interpreter):
        return [interpreter]
    return interpreter.split()


def python_version_tuple(command: list[str]) -> tuple[int, int] | None:
    """Ask an interpreter for its ``(major, minor)`` version by running it."""
    out = command_output(
        command + ["-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
        timeout=20,
    )
    match = re.match(r"(\d+)\.(\d+)", out)
    if match:
        return (int(match.group(1)), int(match.group(2)))
    return None


def choose_python(injected: str | None = None, *, check_only: bool = False) -> str | None:
    """Find an interpreter >= the project minimum (3.12), preferring newest.

    ``injected`` is the absolute path produced by the shell/PowerShell bootstrap
    and passed through ``--python`` / ``ARCANUM_BOOTSTRAP_PYTHON``. It is
    preferred over any host interpreter.

    Under ``check_only`` no external tool is executed at all: an injected path
    is accepted only when it exists on disk, and without an injected path no
    interpreter is reported so the caller can emit an Unsupported / Needs
    manual action row without any filesystem writes. Version verification is
    deferred to a real (non-check) run.
    """
    if injected:
        candidate = injected.strip()
        if not candidate:
            return None
        if check_only:
            return candidate if Path(candidate).expanduser().exists() else None
        version = python_version_tuple(interpreter_command(candidate))
        if version is not None and version >= MIN_PYTHON:
            return candidate
        return None
    if check_only:
        return None

    candidates: list[str] = []
    if IS_WINDOWS:
        candidates.append("py -3.12")
    for minor in range(15, 11, -1):
        candidates.append(f"python3.{minor}")
    candidates.extend(["python3", "python", sys.executable])
    seen: set[str] = set()
    for candidate in candidates:
        parts = interpreter_command(candidate)
        exe = shutil.which(parts[0])
        if not exe:
            continue
        command = [exe, *parts[1:]]
        version = python_version_tuple(command)
        if version is not None and version >= MIN_PYTHON:
            key = " ".join(command)
            if key not in seen:
                seen.add(key)
                return key
    return None


def step_python(
    paths: Paths,
    manifest: dict[str, Any],
    reporter: Reporter,
    state: dict[str, Any],
    *,
    check_only: bool,
    skip_tests: bool,
    injected_python: str | None = None,
) -> None:
    injected = injected_python or os.environ.get(BOOTSTRAP_PYTHON_ENV)
    py = choose_python(injected, check_only=check_only)
    if injected:
        if py:
            if check_only:
                reporter.add(
                    "Bootstrap Python",
                    "Needs manual action",
                    f"bootstrapped interpreter at {injected} found; version verification requires a non-check run",
                )
            else:
                reporter.add("Bootstrap Python", "Verified", f"bootstrapped interpreter: {injected}")
        else:
            reporter.add("Bootstrap Python", "Unsupported", f"bootstrapped interpreter is not usable: {injected}")
    if not py:
        reporter.add("Python", "Unsupported", "no Python >=3.12 interpreter found (pyproject.toml requires >=3.12)")
        return
    reporter.add("Python", "Verified", f"project interpreter: {py}")
    state["python"] = {"interpreter": py}

    if paths.venv_python.exists():
        if check_only:
            reporter.add("Python venv", "Already present", f"{paths.venv_python} (version not verified in --check)")
        else:
            current = command_output([paths.venv_python, "--version"])
            reporter.add("Python venv", "Already present", f"{paths.venv_python} ({current})")
    elif check_only:
        reporter.add("Python venv", "Needs manual action", f"not created: {paths.venv}")
        return
    else:
        paths.home.mkdir(parents=True, exist_ok=True)
        parts = interpreter_command(py)
        result = run([*parts, "-m", "venv", str(paths.venv)], timeout=600)
        if result is None or result.returncode != 0:
            reporter.add("Python venv", "Failed", (result.stdout if result else "")[-400:])
            return
        reporter.add("Python venv", "Installed", str(paths.venv))

    packages = manifest.get("python", {}).get("packages", {})
    wanted: dict[str, str] = {}
    for group in ("runtime", "dev"):
        for entry in packages.get(group, []):
            if entry.get("pinned"):
                wanted[entry["name"]] = str(entry["pinned"])
    if not wanted:
        reporter.add("Python deps", "Failed", "manifest has no pinned Python packages")
        return
    if check_only:
        reporter.add("Python deps", "Needs manual action", "dependency verification requires the venv (re-run without --check)")
        return
    missing: list[str] = []
    for name, version in wanted.items():
        installed = command_output(
            [paths.venv_python, "-c", f"import importlib.metadata as m; print(m.version('{name}'))"],
            timeout=60,
        )
        if installed != version:
            missing.append(f"{name}=={version}")
    if missing:
        reporter.add("Python deps", "Installed", "installing " + ", ".join(missing))
        result = run(
            [paths.venv_python, "-m", "pip", "install", "--disable-pip-version-check", *missing],
            timeout=900,
        )
        if result is None or result.returncode != 0:
            reporter.add("Python deps", "Failed", (result.stdout if result else "")[-600:])
            return
    reporter.add("Python deps", "Verified", f"{len(wanted)} pinned packages present")

    # Relocate package code into the runtime home so Settings.base_dir is <home>.
    if not check_only:
        copied = copy_tree(paths.repo / "src" / "arcanum", paths.home / "src" / "arcanum")
        copy_tree(paths.repo / "scripts", paths.home / "scripts", ignore={"__pycache__"})
        copy_tree(paths.repo / "integrations", paths.home / "integrations", ignore={"__pycache__"})
        reporter.add("Runtime copy", "Installed", f"{copied} Python files under {paths.home / 'src'}")

    env = {
        "ARCANUM_HOME": str(paths.home),
        "PYTHONPATH": str(paths.home / "src"),
    }
    result = run(
        [paths.venv_python, "-m", "pip", "check"],
        env={**os.environ, **env},
        timeout=120,
    )
    if result is None or result.returncode != 0:
        reporter.add("Python integrity", "Failed", (result.stdout if result else "")[-400:])
    else:
        reporter.add("Python integrity", "Verified", "pip check passed")

    if skip_tests or check_only:
        reporter.add("Python tests", "Skipped" if skip_tests else "Needs manual action", "run pytest manually to confirm PYTHONPATH=src")
        return
    result = run(
        [paths.venv_python, "-m", "pytest", "-q", "--rootdir", str(paths.repo), str(paths.repo / "tests")],
        cwd=paths.repo,
        env={**os.environ, **env},
        timeout=900,
    )
    if result is None or result.returncode != 0:
        reporter.add("Python tests", "Failed", (result.stdout if result else "")[-800:])
    else:
        reporter.add("Python tests", "Verified", "PYTHONPATH=<home>/src pytest suite passed")
    state["python"] = {"interpreter": py, "packages": wanted}


def step_databases(paths: Paths, reporter: Reporter, state: dict[str, Any], *, check_only: bool) -> None:
    db_names = ["arcanum.db", "arcanum-approvals.sqlite", "arcanum-grants.sqlite", "arcanum-permission-audit.sqlite", "arcanum-tabs.sqlite", "data/hackbots.db"]
    if check_only:
        present = [name for name in db_names if (paths.home / name).exists()]
        reporter.add(
            "Databases",
            "Verified" if len(present) == len(db_names) else "Needs manual action",
            f"{len(present)}/{len(db_names)} expected database files present under {paths.home}",
        )
        return
    if not paths.venv_python.exists():
        reporter.add("Databases", "Failed", "venv python missing; cannot initialize schemas")
        return
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    code = r'''
import os, sys
from pathlib import Path
home = Path(os.environ["ARCANUM_HOME"])
sys.path.insert(0, str(home / "src"))
from arcanum.config.settings import Settings
from arcanum.memory.database import Database
from arcanum.runtime import TabRegistry
from arcanum.permissions.approval_queue import ApprovalQueue
from arcanum.permissions.permission_audit import PermissionAuditLog
from arcanum.permissions.permission_manager import PermissionStore
s = Settings(base_dir=home)
objs = [
    Database(s.db_path),
    TabRegistry(s.tab_registry_path),
    ApprovalQueue(s.approval_db),
    PermissionAuditLog(home / "arcanum-permission-audit.sqlite"),
    PermissionStore(home / "arcanum-grants.sqlite"),
]
for obj in objs:
    close = getattr(obj, "close", None)
    if close:
        close()
print("ok", len(objs))
'''
    result = run(
        [paths.venv_python, "-c", code],
        env={**os.environ, "ARCANUM_HOME": str(paths.home), "PYTHONPATH": str(paths.home / "src")},
        timeout=180,
    )
    if result is None or result.returncode != 0 or "ok 5" not in (result.stdout or ""):
        reporter.add("Databases", "Failed", (result.stdout if result else "")[-600:])
        return
    reporter.add("Databases", "Verified", "CREATE TABLE IF NOT EXISTS / incremental migrations; existing data untouched")
    state["databases_initialized"] = True


def step_tool(
    name: str,
    manifest_section: dict[str, Any],
    paths: Paths,
    reporter: Reporter,
    *,
    check_only: bool,
    skip_downloads: bool,
    timeout: int,
) -> Path | None:
    expected = str(manifest_section.get("version") or manifest_section.get("tag") or "")
    key = asset_key()
    assets = manifest_section.get("assets", {})
    if key not in assets:
        reporter.add(name, "Unsupported", f"no pinned asset for {key}")
        return None
    asset = assets[key]

    existing = None
    on_path = shutil.which(name) if not check_only else None
    for candidate in [paths.bin / f"{name}{SH_SUFFIX}", Path(on_path) if on_path else None]:
        if candidate and candidate.exists():
            if check_only:
                # Running a tool such as `opencode --version` creates config,
                # cache and state directories. --check only does static checks.
                existing = candidate
                continue
            observed = version_of(candidate)
            if expected and expected.lstrip("v") in observed.lstrip("v"):
                reporter.add(name, "Verified", f"{observed} at {candidate}")
                return candidate
            if observed:
                existing = candidate
    if check_only:
        if existing:
            reporter.add(
                name,
                "Needs manual action",
                f"{existing} present; version verification requires a non-check run (pinned {expected})",
            )
        else:
            reporter.add(name, "Needs manual action", f"not found; pinned {expected} asset: {asset.get('url')}")
        return None
    if skip_downloads:
        reporter.add(name, "Skipped", "--skip-downloads; pinned asset not fetched")
        return None

    archive = download_asset(asset, paths.cache, reporter, name, timeout=timeout)
    if not archive:
        return None
    version_dir = paths.tools / name / (expected or "pin")
    binary_name = f"{name}{EXE}" if IS_WINDOWS else name
    binary = find_file(version_dir, {binary_name}) if version_dir.exists() else None
    if not binary:
        try:
            if archive.suffix == ".zip" or archive.name.endswith(".zip"):
                extract_zip(archive, version_dir)
            elif archive.name.endswith((".tar.gz", ".tgz")):
                extract_tar(archive, version_dir)
            else:
                raise RuntimeError(f"unsupported archive format: {archive.name}")
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, tarfile.TarError) as exc:
            reporter.add(name, "Failed", f"extraction failed: {exc}")
            return None
        binary = find_file(version_dir, {binary_name})
    if not binary:
        reporter.add(name, "Failed", f"could not locate {binary_name} inside {version_dir}")
        return None
    if not IS_WINDOWS:
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    backups: list[str] = []
    wrapper = simple_wrapper(paths.bin / name, binary, backups)
    observed = version_of(wrapper)
    reporter.add(
        name,
        "Verified" if (not expected or expected.lstrip("v") in observed.lstrip("v")) else "Needs manual action",
        f"installed {expected if expected else ''} at {binary}; wrapper {wrapper}; reports {observed or 'no --version output'}",
    )
    return wrapper


def find_maestro_pair(paths: Paths) -> tuple[Path | None, Path | None]:
    """Find (electron binary, bundled maestro-cli.js) from known install roots."""
    roots = [
        paths.tools / "maestro",
        Path("/opt/Maestro"),
        Path("/opt/maestro"),
        Path("/Applications/Maestro.app/Contents"),
        Path.home() / "Applications" / "Maestro.app" / "Contents",
        Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Programs" / "Maestro",
        Path(os.environ.get("PROGRAMFILES", "C:\\Program Files")) / "Maestro",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        js = find_file(root, {"maestro-cli.js"})
        if not js:
            continue
        app_root = js.parent.parent
        for electron_name in ("maestro", "Maestro", "Maestro.exe", "maestro.exe"):
            electron = app_root / electron_name
            if electron.is_file():
                return electron, js
        for candidate in find_candidates(root, {"maestro", "Maestro", "Maestro.exe", "maestro.exe"}):
            return candidate, js
    return None, None


def find_candidates(root: Path, names: set[str]) -> list[Path]:
    return sorted(
        [p for p in root.rglob("*") if p.is_file() and p.name in names],
        key=lambda p: len(p.relative_to(root).parts),
    )


def step_maestro(
    paths: Paths,
    manifest: dict[str, Any],
    reporter: Reporter,
    state: dict[str, Any],
    *,
    check_only: bool,
    skip_downloads: bool,
    timeout: int,
) -> None:
    section = manifest.get("maestro", {})
    expected = str(section.get("expected_version") or section.get("tag") or "")
    key = asset_key()
    assets = section.get("assets", {})
    if key not in assets:
        reason = "Maestro v1.1.1-RC has no Windows ARM64 release asset" if key == "windows-arm64" else f"no official asset for {key}"
        reporter.add("Maestro", "Unsupported", reason)
        return
    platform_assets = assets[key]

    # Explicitly do not install the mobile.dev Maestro CLI.
    reporter.add("Maestro source", "Verified", "official RunMaestro/Maestro assets only; mobile.dev CLI not installed")

    electron, js = find_maestro_pair(paths)
    wrapper = paths.bin / f"maestro-cli{SH_SUFFIX}"

    if check_only:
        # Never run the Electron/CLI wrapper in a dry run: --version can write
        # into config/cache/state directories. Use file-based detection only.
        if wrapper.exists() and wrapper.is_file():
            reporter.add(
                "Maestro",
                "Needs manual action",
                f"wrapper present at {wrapper}; version verification requires a non-check run",
            )
        elif electron and js:
            reporter.add("Maestro", "Needs manual action", f"bundled CLI found at {js}; version not verified in --check")
        else:
            sample = next(iter(platform_assets.values()), {}) if isinstance(platform_assets, dict) else {}
            reporter.add(
                "Maestro",
                "Needs manual action",
                f"not installed; official asset URL: {sample.get('url', section.get('release_url', ''))}",
            )
        return

    def bundled_version(binary: Path, script: Path) -> str:
        result = run(
            [binary, script, "--version"],
            env={**os.environ, "ELECTRON_RUN_AS_NODE": "1"},
            timeout=60,
        )
        out = (result.stdout or "") if result else ""
        match = re.search(r"(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)", out)
        return match.group(1) if match else ""

    if wrapper.exists() and wrapper.is_file():
        observed = version_of(wrapper)
        if observed and expected.lstrip("v") in observed.lstrip("v"):
            reporter.add("Maestro", "Verified", f"maestro-cli {observed} via {wrapper}")
            state["maestro"] = {"version": observed, "wrapper": str(wrapper)}
            return
    if electron and js:
        observed = bundled_version(electron, js)
        if expected and expected in observed:
            reporter.add("Maestro", "Verified", f"bundled CLI {expected} at {js}")
            return
        if not skip_downloads:
            backups: list[str] = []
            made = maestro_wrapper(paths.bin / "maestro-cli", electron, js, backups)
            state.setdefault("backups", []).extend(backups)
            state["maestro"] = {"version": expected, "wrapper": str(made), "electron": str(electron), "cli_js": str(js)}
            state.setdefault("managed_paths", []).extend([str(made), str(paths.bin / "maestro-cli.ps1")])
            version_check = version_of(made)
            reporter.add(
                "Maestro",
                "Verified" if expected in version_check else "Needs manual action",
                f"bundled CLI wrapper {made}; --version -> {version_check or 'unknown'}",
            )
            return

    if check_only:
        sample = next(iter(platform_assets.values()), {}) if isinstance(platform_assets, dict) else {}
        reporter.add("Maestro", "Needs manual action", f"not installed; official asset URL: {sample.get('url', section.get('release_url', ''))}")
        return
    if skip_downloads:
        reporter.add("Maestro", "Skipped", "--skip-downloads; no Maestro install attempted")
        return

    chosen: tuple[str, dict[str, Any]] | None = None
    if key.startswith("linux"):
        if shutil.which("dpkg-deb") and platform_assets.get("deb"):
            chosen = ("deb", platform_assets["deb"])
        elif platform_assets.get("appimage"):
            chosen = ("appimage", platform_assets["appimage"])
        elif platform_assets.get("rpm"):
            chosen = ("rpm", platform_assets["rpm"])
    elif key.startswith("darwin"):
        chosen = ("zip", platform_assets["zip"])
    elif key == "windows-x64":
        chosen = ("portable", platform_assets["portable"])

    if not chosen:
        reporter.add("Maestro", "Needs manual action", f"no automated path for {key}; download from {section.get('release_url')}")
        return
    kind, asset = chosen
    archive = download_asset(asset, paths.cache, reporter, "Maestro", timeout=timeout)
    if not archive:
        return

    if kind == "portable":
        reporter.add(
            "Maestro",
            "Needs manual action",
            f"Windows Maestro installer downloaded and checksum-verified: {archive}. Run it as the current user, then re-run install.ps1 to verify. No admin escalation attempted.",
        )
        return
    version_dir = paths.tools / "maestro" / expected
    if kind == "deb":
        ok = version_dir.exists() or extract_deb(archive, version_dir)
    elif kind == "appimage":
        ok = version_dir.exists() or extract_appimage(archive, version_dir)
    else:
        try:
            if not version_dir.exists():
                extract_zip(archive, version_dir)
            ok = True
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
            reporter.add("Maestro", "Failed", f"macOS zip extraction failed: {exc}")
            return
    if not ok:
        reporter.add(
            "Maestro",
            "Needs manual action",
            f"could not extract {kind} without admin rights; install manually from {section.get('release_url')} (SHA-256 in config/dependencies.json)",
        )
        return
    electron, js = find_file(version_dir, {"maestro", "Maestro", "maestro.exe", "Maestro.exe"}), find_file(version_dir, {"maestro-cli.js"})
    if not electron or not js:
        reporter.add("Maestro", "Needs manual action", f"extracted {archive.name} but bundled CLI not found; inspect {version_dir}")
        return
    backups: list[str] = []
    made = maestro_wrapper(paths.bin / "maestro-cli", electron, js, backups)
    state.setdefault("backups", []).extend(backups)
    state["maestro"] = {"version": expected, "wrapper": str(made), "electron": str(electron), "cli_js": str(js)}
    state.setdefault("managed_paths", []).extend([str(made), str(paths.bin / "maestro-cli.ps1")])
    version_check = version_of(made)
    reporter.add(
        "Maestro",
        "Verified" if expected in version_check else "Needs manual action",
        f"installed from {asset.get('asset')}; wrapper {made}; --version -> {version_check or 'unknown'}",
    )


def step_bun(paths: Paths, manifest: dict[str, Any], reporter: Reporter, skip_downloads: bool, timeout: int) -> Path | None:
    return step_tool(
        "bun",
        manifest.get("bun", {}),
        paths,
        reporter,
        check_only=False,
        skip_downloads=skip_downloads,
        timeout=timeout,
    )


def step_opencode(paths: Paths, manifest: dict[str, Any], reporter: Reporter, skip_downloads: bool, timeout: int) -> Path | None:
    return step_tool(
        "opencode",
        manifest.get("opencode", {}),
        paths,
        reporter,
        check_only=False,
        skip_downloads=skip_downloads,
        timeout=timeout,
    )


def step_dashboard(
    paths: Paths,
    manifest: dict[str, Any],
    reporter: Reporter,
    bun: Path | None,
    *,
    check_only: bool,
    start: bool,
    state: dict[str, Any],
) -> None:
    source = paths.repo / "apps" / "dashboard"
    dest = paths.home / "apps" / "dashboard"
    health = f"http://127.0.0.1:{manifest.get('bun', {}).get('dashboard', {}).get('default_port', 13337)}/api/health"
    if not source.is_dir():
        reporter.add("Dashboard", "Failed", f"missing {source}")
        return
    if check_only:
        reporter.add("Dashboard source", "Verified" if (source / "server.ts").exists() else "Failed", str(source))
        reporter.add("Dashboard health", "Needs manual action", f"runtime health not probed in --check: {health}")
        return
    copy_tree(source, dest)
    reporter.add("Dashboard source", "Installed", f"copied to {dest} (no repo files changed)")
    if not bun:
        reporter.add("Dashboard deps", "Failed", "Bun unavailable; cannot install dashboard dependencies")
        return
    env = {
        **os.environ,
        "ARCANUM_HOME": str(paths.home),
        "DATA_DIR": str(paths.data_dir),
        "ARCANUM_DB": str(paths.home / "arcanum.db"),
        "PYTHONPATH": str(paths.home / "src"),
    }
    result = run([bun, "install", "--frozen-lockfile"], cwd=dest, env=env, timeout=600)
    if result is None or result.returncode != 0:
        reporter.add("Dashboard deps", "Failed", (result.stdout if result else "")[-600:])
        return
    reporter.add("Dashboard deps", "Verified", "bun install --frozen-lockfile succeeded")

    db_result = run(
        [bun, "-e", 'import { DB_PATH } from "./db.ts"; console.log(DB_PATH)'],
        cwd=dest,
        env=env,
        timeout=180,
    )
    if db_result is None or db_result.returncode != 0:
        reporter.add("Dashboard DB", "Failed", (db_result.stdout if db_result else "")[-500:])
    else:
        reporter.add("Dashboard DB", "Verified", f"hackbots.db initialized at {paths.data_dir}")

    if http_ok(health):
        reporter.add("Dashboard health", "Verified", f"already listening: {health}")
        return
    if not start:
        reporter.add("Dashboard health", "Needs manual action", f"not running; start with: cd {dest} && bun run server.ts")
        return
    paths.logs.mkdir(parents=True, exist_ok=True)
    logfile = paths.logs / "dashboard.log"
    command = [bun, "run", "server.ts"]
    handle = logfile.open("ab")
    popen_kwargs: dict[str, Any] = {
        "cwd": str(dest),
        "env": env,
        "stdout": handle,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
    }
    if IS_WINDOWS:
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        popen_kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen([str(c) for c in command], **popen_kwargs)
    except OSError as exc:
        reporter.add("Dashboard health", "Failed", f"could not start server: {exc}")
        return
    (paths.home / "dashboard.pid").write_text(str(proc.pid), encoding="utf-8")
    ready = False
    for _ in range(20):
        if http_ok(health):
            ready = True
            break
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    if ready:
        reporter.add("Dashboard health", "Verified", f"server pid {proc.pid}; {health}; log {logfile}")
        state["dashboard"] = {"pid_file": str(paths.home / "dashboard.pid"), "log": str(logfile), "health": health}
    else:
        reporter.add("Dashboard health", "Failed", f"server did not become healthy; see {logfile}")


def step_skills(
    paths: Paths,
    manifest: dict[str, Any],
    reporter: Reporter,
    *,
    check_only: bool,
    state: dict[str, Any],
) -> None:
    sources = [paths.repo / "skills" / "arcanum"]
    vendor = manifest.get("skills", {}).get("vendor", {})
    vendor_missing: list[str] = []
    for name, info in vendor.items():
        local = paths.repo / str(info.get("local_path", ""))
        if local.is_dir():
            sources.append(local)
        else:
            vendor_missing.append(f"{name} (pin {info.get('pinned_commit')}, {info.get('upstream')})")
    registry = paths.repo / "skills" / "registry" / "registry.json"
    registry_entries: list[str] = []
    if registry.exists():
        try:
            data = json.loads(registry.read_text(encoding="utf-8"))
            entries = data.get("skills", data) if isinstance(data, dict) else data
            if isinstance(entries, dict):
                entries = list(entries.values())
            for entry in entries:
                if isinstance(entry, dict) and entry.get("path"):
                    candidate = paths.repo / str(entry["path"])
                    if candidate.is_dir():
                        sources.append(candidate)
                    else:
                        registry_entries.append(str(entry.get("name", entry["path"])))
                elif isinstance(entry, str):
                    candidate = paths.repo / "skills" / entry
                    if candidate.is_dir():
                        sources.append(candidate)
                    else:
                        registry_entries.append(entry)
            reporter.add("Skill registry", "Verified", f"read {registry.relative_to(paths.repo)}; {len(sources)} local skill roots")
        except (OSError, ValueError) as exc:
            reporter.add("Skill registry", "Needs manual action", f"unreadable {registry}: {exc}")
    else:
        reporter.add("Skill registry", "Needs manual action", f"not present yet: {registry} (created by another agent; non-fatal)")

    if check_only:
        reporter.add("Skills", "Needs manual action", f"{len(sources)} local skill roots available for install")
        return
    home_skills = paths.home / "skills"
    oc_skills = paths.opencode_config / "skills"
    total = 0
    for source in sources:
        total += copy_tree(source, home_skills)
        total += copy_tree(source, oc_skills)
    reporter.add("Skills", "Installed", f"installed {total} skill files to {home_skills} and {oc_skills}")
    state["skills"] = {"roots": [str(s.relative_to(paths.repo)) for s in sources if is_under(s, paths.repo)]}
    if vendor_missing:
        reporter.add("Vendor skills", "Needs manual action", "not vendored: " + "; ".join(vendor_missing))
    if registry_entries:
        reporter.add("Registry skills", "Needs manual action", "listed in registry but missing locally: " + ", ".join(registry_entries))


def placeholder_map(paths: Paths, legacy_dispatch: str) -> dict[str, str]:
    return {
        "{{ARCANUM_HOME}}": str(paths.home),
        "{{ARCANUM_TAB_DISPATCH}}": str(paths.bin / "arcanum-tab-dispatch"),
        "{{LEGACY_TAB_DISPATCH}}": legacy_dispatch,
    }


def render_placeholders(text: str, paths: Paths, template: dict[str, Any], legacy_dispatch: str) -> str:
    for key, value in placeholder_map(paths, legacy_dispatch).items():
        text = text.replace(key, value)
    return text


def substitute_tree(value: Any, mapping: dict[str, str]) -> Any:
    """Replace placeholders in strings without corrupting JSON escaping."""
    if isinstance(value, str):
        for key, replacement in mapping.items():
            value = value.replace(key, replacement)
        return value
    if isinstance(value, list):
        return [substitute_tree(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: substitute_tree(item, mapping) for key, item in value.items()}
    return value


def extract_legacy_dispatch(template: dict[str, Any]) -> str:
    lead = template.get("agents", {}).get("hackbot-lead", {}).get("system", "")
    match = re.search(r"(/[^\s\"'<>|]+\.arcanum/scripts/arcanum-tab-dispatch|/[^\s\"'<>|]+/arcanum-tab-dispatch)", lead)
    return match.group(1) if match else ""


def step_agents(
    paths: Paths,
    manifest: dict[str, Any],
    reporter: Reporter,
    *,
    check_only: bool,
    state: dict[str, Any],
) -> None:
    """Write exact SYSTEM prompts + permissions into the OpenCode config."""
    template_path = paths.repo / "config" / "opencode.agents.json"
    agents_dir = paths.repo / "config" / "agents"
    try:
        template = json.loads(template_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        reporter.add("OpenCode agents", "Failed", f"cannot read {template_path}: {exc}")
        return
    systems: dict[str, str] = {}
    for name in AGENTS:
        md = agents_dir / f"{name}.md"
        if not md.exists():
            reporter.add("OpenCode agents", "Failed", f"missing agent definition {md}")
            return
        body = md.read_text(encoding="utf-8")
        prompt = body.split("---\n\n", 1)[1] if body.startswith("---\n") else body
        if prompt != template.get("agents", {}).get(name, {}).get("system"):
            reporter.add("OpenCode agents", "Failed", f"prompt mismatch between {md.name} and opencode.agents.json for {name}")
            return
        systems[name] = prompt
    reporter.add("Agent prompts", "Verified", "five exact SYSTEM prompts extracted from config/agents (source: wire_maestro_agents.py)")

    legacy = extract_legacy_dispatch(template)
    if check_only:
        config_path = paths.opencode_config / "opencode.json"
        if not config_path.exists():
            reporter.add("OpenCode config", "Needs manual action", f"no config at {config_path}")
            return
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            reporter.add("OpenCode config", "Failed", f"unreadable {config_path}: {exc}")
            return
        missing = [name for name in AGENTS if existing.get("agents", {}).get(name, {}).get("system") != systems[name]]
        reporter.add(
            "OpenCode config",
            "Verified" if not missing else "Needs manual action",
            f"{len(AGENTS) - len(missing)}/{len(AGENTS)} agents wired" if missing else "all five agents wired with matching prompts",
        )
        return

    paths.opencode_config.mkdir(parents=True, exist_ok=True)
    config_path = paths.opencode_config / "opencode.json"
    existing: dict[str, Any] = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            backup_file(config_path)
            existing = {}
            reporter.add("OpenCode config", "Needs manual action", "existing opencode.json was unreadable; backed up before rewrite")
    backups: list[str] = []
    before = json.dumps(existing, sort_keys=True)
    agents = existing.setdefault("agents", {})
    for name in AGENTS:
        source_agent = template["agents"][name]
        entry = agents.setdefault(name, {})
        for field_name in source_agent.get("managed_fields", ["system", "permissions"]):
            value = source_agent.get(field_name)
            if field_name == "permissions":
                value = substitute_tree(value, placeholder_map(paths, legacy))
            elif isinstance(value, str):
                value = render_placeholders(value, paths, template, legacy)
            entry[field_name] = value
        md = agents_dir / f"{name}.md"
        dest_md = paths.opencode_config / "agents" / f"{name}.md"
        if install_file(md, dest_md, backups):
            state.setdefault("managed_paths", []).append(str(dest_md))
    if json.dumps(existing, sort_keys=True) != before:
        if config_path.exists():
            backup = backup_file(config_path)
            if backup:
                backups.append(str(backup))
        write_json(config_path, existing)
        reporter.add("OpenCode config", "Installed", f"five agents + permissions written to {config_path}")
    else:
        reporter.add("OpenCode config", "Already present", "agents and permissions already match")
    state.setdefault("backups", []).extend(backups)
    state["opencode_config"] = str(config_path)
    state["opencode_agents"] = list(AGENTS)
    if legacy and not Path(legacy).exists():
        reporter.add(
            "Lead prompt path",
            "Needs manual action",
            f"the verbatim LEAD prompt references {legacy}, which does not exist here; installed wrapper is {paths.bin / 'arcanum-tab-dispatch'} (prompt intentionally not modified)",
        )
    else:
        reporter.add("Lead prompt path", "Verified", legacy or "no legacy path in prompt")


def step_tab_wrapper(paths: Paths, reporter: Reporter, *, check_only: bool, state: dict[str, Any]) -> None:
    script = paths.home / "scripts" / "arcanum-tab-dispatch"
    if not script.exists():
        reporter.add("Tab dispatch wrapper", "Needs manual action", f"missing {script}")
        return
    if check_only:
        target = paths.bin / "arcanum-tab-dispatch"
        reporter.add("Tab dispatch wrapper", "Verified" if target.exists() else "Needs manual action", str(target))
        return
    backups: list[str] = []
    wrapper = python_script_wrapper(
        paths.bin / "arcanum-tab-dispatch",
        paths.venv_python,
        script,
        {
            "ARCANUM_HOME": str(paths.home),
            "ARCANUM_ROOT": str(paths.home),
            "PYTHONPATH": str(paths.home / "src"),
        },
        backups,
    )
    state.setdefault("managed_paths", []).extend([str(wrapper), str(paths.bin / "arcanum-tab-dispatch.ps1")])
    state.setdefault("backups", []).extend(backups)
    reporter.add("Tab dispatch wrapper", "Installed", f"{wrapper} (registry-aware, no raw --new-tab)")


def step_playbooks(paths: Paths, manifest: dict[str, Any], reporter: Reporter, *, check_only: bool, state: dict[str, Any]) -> None:
    source = paths.repo / "workflows" / "maestro" / "playbooks"
    if not source.is_dir():
        reporter.add("Maestro playbooks", "Failed", f"missing {source}")
        return
    cycle = paths.maestro_config / "playbooks" / f"{time.strftime('%Y-%m-%d')}-Arcanum-Cycle"
    expected = sorted(p.name for p in source.glob("*.md"))
    if check_only:
        found = list((paths.maestro_config / "playbooks").glob("*-Arcanum-Cycle/*.md")) if (paths.maestro_config / "playbooks").exists() else []
        reporter.add("Maestro playbooks", "Verified" if found else "Needs manual action", f"{len(found)} installed cycle playbooks")
        return
    if cycle.exists() and sorted(p.name for p in cycle.glob("*.md")) == expected:
        reporter.add("Maestro playbooks", "Already present", str(cycle))
        return
    target = cycle
    counter = 1
    while target.exists() and sorted(p.name for p in target.glob("*.md")) != expected:
        target = paths.maestro_config / "playbooks" / f"{time.strftime('%Y-%m-%d')}-Arcanum-Cycle-{counter}"
        counter += 1
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for item in sorted(source.glob("*.md")):
        dest = target / item.name
        if not dest.exists():
            shutil.copy2(item, dest)
            copied += 1
    write_json(target / PLAYBOOK_MARKER, {"installer": "arcanum", "source": str(source), "installed_at": now_iso()})
    state.setdefault("managed_paths", []).append(str(target))
    reporter.add("Maestro playbooks", "Installed", f"{copied} playbook(s) copied to {target}; existing Maestro data preserved")


def discord_template(paths: Paths, manifest: dict[str, Any]) -> str:
    section = manifest.get("discord", {})
    for candidate in section.get("template_candidates", []):
        path = paths.repo / str(candidate)
        if path.exists():
            return path.read_text(encoding="utf-8")
    variables = [
        ("DISCORD_BOT_TOKEN", ""),
        ("DISCORD_WEBHOOK_URL", ""),
        ("ALLOWED_USER_IDS", ""),
        ("ALLOWED_CHANNEL_IDS", ""),
        ("MAESTRO_AGENT", "hackbot-lead"),
        ("AGENT_TIMEOUT", "300"),
        ("ARCANUM_HOME", str(paths.home)),
        ("ARCANUM_ROOT", str(paths.home)),
        ("PYTHONPATH", str(paths.home / "src")),
        ("MAESTRO_CLI", str(paths.bin / f"maestro-cli{SH_SUFFIX}")),
        ("HARNESS_DB", str(paths.data_dir / "hackbots.db")),
        ("ARCANUM_DB", str(paths.home / "arcanum.db")),
        ("DATA_DIR", str(paths.data_dir)),
        ("DASHBOARD_TOKEN", ""),
    ]
    lines = [
        "# Arcanum Discord bridge configuration.",
        "# Generated from .env.example when present; otherwise from bridge.py variables.",
        "# NEVER commit this file. Mode 0600 on POSIX systems.",
        "",
    ]
    lines.extend(f"{key}={value}" for key, value in variables)
    return "\n".join(lines) + "\n"


def step_discord(
    paths: Paths,
    manifest: dict[str, Any],
    reporter: Reporter,
    *,
    check_only: bool,
    interactive: bool,
    state: dict[str, Any],
) -> None:
    dest = paths.home / "discord" / ".env"
    if check_only:
        values = parse_dotenv(dest)
        required = manifest.get("discord", {}).get("required_variables", [])
        missing = [key for key in required if not values.get(key)]
        reporter.add(
            "Discord config",
            "Needs authentication" if missing else "Verified",
            f"{dest}: missing {', '.join(missing)}" if missing else "all required variables set",
        )
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        write_text(dest, discord_template(paths, manifest), mode=0o600)
        reporter.add("Discord config", "Installed", f"template created at {dest} (mode 0600 on POSIX)")
    else:
        reporter.add("Discord config", "Already present", f"{dest} left unchanged")
    values = parse_dotenv(dest)
    required = manifest.get("discord", {}).get("required_variables", [])
    missing = [key for key in required if not values.get(key)]
    if not interactive:
        reporter.add("Discord auth", "Needs authentication", "non-interactive: set DISCORD_BOT_TOKEN / ALLOWED_* in " + str(dest))
        return
    if not missing:
        reporter.add("Discord auth", "Verified", "required variables already present in " + str(dest))
        return
    try:
        import getpass

        token = getpass.getpass("Discord bot token (Enter to skip): ").strip()
    except (EOFError, KeyboardInterrupt):
        token = ""
    if not token:
        reporter.add("Discord auth", "Needs authentication", "no token supplied; edit " + str(dest))
        return
    text = dest.read_text(encoding="utf-8")
    lines = []
    replaced = False
    for line in text.splitlines():
        if line.startswith("DISCORD_BOT_TOKEN=") and not replaced:
            lines.append("DISCORD_BOT_TOKEN=" + token)
            replaced = True
        else:
            lines.append(line)
    if not replaced:
        lines.append("DISCORD_BOT_TOKEN=" + token)
    write_text(dest, "\n".join(lines) + "\n", mode=0o600)
    reporter.add("Discord auth", "Verified", f"token stored only in {dest} (never logged)")


def step_provider_auth(
    paths: Paths,
    reporter: Reporter,
    *,
    check_only: bool,
    interactive: bool,
    opencode: Path | None,
) -> None:
    if check_only:
        reporter.add("Provider auth", "Needs authentication", "verified interactively by OpenCode; use `opencode auth login`")
        return
    if not opencode:
        reporter.add("Provider auth", "Needs manual action", "OpenCode unavailable; run `opencode auth login` after install")
        return
    if not interactive:
        reporter.add("Provider auth", "Needs authentication", "opencode auth login is interactive; run it manually")
        return
    try:
        answer = input("Authenticate an LLM provider with OpenCode now? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer not in {"y", "yes"}:
        reporter.add("Provider auth", "Needs authentication", "deferred; run `opencode auth login`")
        return
    result = run([opencode, "auth", "login"], interactive=True, timeout=None)
    reporter.add(
        "Provider auth",
        "Verified" if result is not None and result.returncode == 0 else "Needs authentication",
        "opencode auth login completed" if result is not None and result.returncode == 0 else "opencode auth login did not complete",
    )


# ---------------------------------------------------------------------------
# Install / uninstall orchestration
# ---------------------------------------------------------------------------

def save_state(paths: Paths, state: dict[str, Any], manifest: dict[str, Any], reporter: Reporter) -> None:
    state.update(
        {
            "schema_version": 1,
            "installed_at": state.get("installed_at", now_iso()),
            "updated_at": now_iso(),
            "engine_version": ENGINE_VERSION,
            "home": str(paths.home),
            "prefix": str(paths.prefix),
            "repo": str(paths.repo),
            "platform": platform_key(),
            "opencode_version": manifest.get("opencode", {}).get("tag"),
            "maestro_version": manifest.get("maestro", {}).get("tag"),
            "bun_version": manifest.get("bun", {}).get("version"),
        }
    )
    managed = list(dict.fromkeys(state.get("managed_paths", [])))
    safe: list[str] = []
    for item in managed:
        path = Path(item)
        if (
            is_under(path, paths.home)
            or is_under(path, paths.prefix)
            or is_under(path, paths.opencode_config)
            or is_under(path, paths.maestro_config)
        ):
            safe.append(str(path))
    state["managed_paths"] = safe
    write_json(paths.state_file, state)


def finish(reporter: Reporter, paths: Paths, args: argparse.Namespace, exit_on_failure: bool = True) -> int:
    reporter.print_matrix()
    if args.check:
        print("Report: not written (--check performs no filesystem changes)")
    else:
        write_json(paths.report_file, {"generated_at": now_iso(), "check": False, "rows": reporter.as_dict()})
        print(f"Report: {paths.report_file}")
    if exit_on_failure and reporter.bad():
        print("RESULT: one or more components are Unsupported/Failed; see matrix above.", file=sys.stderr)
        return 2
    return 0


def cmd_install(paths: Paths, manifest: dict[str, Any], reporter: Reporter, args: argparse.Namespace) -> int:
    supported_platform(manifest, reporter)
    ok = check_environment(paths, reporter)
    state = existing_install(paths, reporter)
    if not ok:
        reporter.add("Preflight", "Failed", "write permissions missing; aborting without changes")
        return finish(reporter, paths, args)
    if args.check:
        step_python(
            paths,
            manifest,
            reporter,
            state,
            check_only=True,
            skip_tests=args.skip_tests,
            injected_python=args.python,
        )
        step_databases(paths, reporter, state, check_only=True)
        step_tool("bun", manifest.get("bun", {}), paths, reporter, check_only=True, skip_downloads=True, timeout=args.timeout)
        step_tool("opencode", manifest.get("opencode", {}), paths, reporter, check_only=True, skip_downloads=True, timeout=args.timeout)
        step_maestro(paths, manifest, reporter, state, check_only=True, skip_downloads=True, timeout=args.timeout)
        step_dashboard(paths, manifest, reporter, None, check_only=True, start=False, state=state)
        step_skills(paths, manifest, reporter, check_only=True, state=state)
        step_agents(paths, manifest, reporter, check_only=True, state=state)
        step_tab_wrapper(paths, reporter, check_only=True, state=state)
        step_playbooks(paths, manifest, reporter, check_only=True, state=state)
        step_discord(paths, manifest, reporter, check_only=True, interactive=False, state=state)
        step_provider_auth(paths, reporter, check_only=True, interactive=False, opencode=None)
        return finish(reporter, paths, args)

    paths.home.mkdir(parents=True, exist_ok=True)
    paths.bin.mkdir(parents=True, exist_ok=True)
    paths.cache.mkdir(parents=True, exist_ok=True)
    paths.logs.mkdir(parents=True, exist_ok=True)

    step_python(
        paths,
        manifest,
        reporter,
        state,
        check_only=False,
        skip_tests=args.skip_tests,
        injected_python=args.python,
    )
    step_databases(paths, reporter, state, check_only=False)
    bun = step_bun(paths, manifest, reporter, args.skip_downloads, args.timeout)
    opencode = step_opencode(paths, manifest, reporter, args.skip_downloads, args.timeout)
    step_maestro(paths, manifest, reporter, state, check_only=False, skip_downloads=args.skip_downloads, timeout=args.timeout)
    step_skills(paths, manifest, reporter, check_only=False, state=state)
    step_agents(paths, manifest, reporter, check_only=False, state=state)
    step_tab_wrapper(paths, reporter, check_only=False, state=state)
    step_playbooks(paths, manifest, reporter, check_only=False, state=state)
    step_dashboard(paths, manifest, reporter, bun, check_only=False, start=not args.no_start_dashboard, state=state)
    step_discord(paths, manifest, reporter, check_only=False, interactive=not args.non_interactive, state=state)
    step_provider_auth(paths, reporter, check_only=False, interactive=not args.non_interactive, opencode=opencode)
    # Installer-created trees (runtime data under data/, logs/, cache/, the
    # Discord .env and the databases are intentionally NOT in this list).
    for relative in ("venv", "tools", "src", "scripts", "apps", "integrations", "bin"):
        state.setdefault("managed_paths", []).append(str(paths.home / relative))
    if paths.prefix != paths.home:
        for relative in ("tools", "bin"):
            state.setdefault("managed_paths", []).append(str(paths.prefix / relative))
    save_state(paths, state, manifest, reporter)
    return finish(reporter, paths, args)


def remove_managed(paths: Paths, state: dict[str, Any], reporter: Reporter, *, check_only: bool) -> None:
    managed = state.get("managed_paths", [])
    preserved = [
        paths.data_dir,
        paths.home / "arcanum.db",
        paths.home / "discord" / ".env",
        paths.logs,
        paths.cache,
    ]
    if check_only:
        reporter.add("Uninstall dry run", "Needs manual action", f"{len(managed)} managed paths would be removed; runtime data preserved")
        return
    removed = 0
    for item in managed:
        path = Path(item)
        if (
            not is_under(path, paths.home)
            and not is_under(path, paths.prefix)
            and not is_under(path, paths.opencode_config)
            and not is_under(path, paths.maestro_config)
        ):
            reporter.add("Uninstall safety", "Failed", f"refusing path outside managed roots: {path}")
            continue
        if any(is_under(path, p) or path == p for p in preserved):
            reporter.add("Uninstall safety", "Skipped", f"runtime data preserved: {path}")
            continue
        if not path.exists():
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed += 1
    reporter.add("Installer artifacts", "Verified", f"removed {removed} managed paths; databases, Discord secrets, logs and cache preserved")


def restore_opencode(paths: Paths, state: dict[str, Any], reporter: Reporter, *, check_only: bool) -> None:
    config = Path(state.get("opencode_config", paths.opencode_config / "opencode.json"))
    agents = state.get("opencode_agents", AGENTS)
    if check_only:
        reporter.add("OpenCode restore", "Needs manual action", f"would remove managed fields for {len(agents)} agents from {config}")
        return
    if not config.exists():
        reporter.add("OpenCode restore", "Skipped", f"{config} does not exist")
        return
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        reporter.add("OpenCode restore", "Failed", f"unreadable {config}: {exc}")
        return
    template_path = paths.repo / "config" / "opencode.agents.json"
    try:
        template = json.loads(template_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        template = {}
    backup = backup_file(config)
    changed = 0
    mapping = placeholder_map(paths, extract_legacy_dispatch(template))
    for name in agents:
        entry = data.get("agents", {}).get(name)
        if not isinstance(entry, dict):
            continue
        original = template.get("agents", {}).get(name, {}).get("system")
        if original is not None and entry.get("system") == original:
            entry.pop("system", None)
            changed += 1
        expected_permissions = substitute_tree(
            template.get("agents", {}).get(name, {}).get("permissions"), mapping
        )
        if expected_permissions is not None and entry.get("permissions") == expected_permissions:
            entry.pop("permissions", None)
            changed += 1
        if not entry:
            data["agents"].pop(name, None)
    if changed:
        write_json(config, data)
    reporter.add(
        "OpenCode restore",
        "Verified" if changed else "Already present",
        f"removed {changed} managed fields from {config}" + (f"; backup {backup}" if backup else ""),
    )


def cmd_uninstall(paths: Paths, manifest: dict[str, Any], reporter: Reporter, args: argparse.Namespace) -> int:
    reporter.add("Uninstall mode", "Verified", "non-destructive: runtime data, databases and secrets are preserved")
    if not paths.state_file.exists():
        reporter.add("Install state", "Needs manual action", f"no {paths.state_file}; nothing tracked for removal")
        return finish(reporter, paths, args)
    try:
        state = json.loads(paths.state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        reporter.add("Install state", "Failed", f"unreadable {paths.state_file}: {exc}")
        return finish(reporter, paths, args)
    reporter.add("Install state", "Verified", f"loaded state from {paths.state_file}")
    restore_opencode(paths, state, reporter, check_only=args.check)
    remove_managed(paths, state, reporter, check_only=args.check)
    if args.check:
        return finish(reporter, paths, args)
    backup_file(paths.state_file)
    paths.state_file.unlink()
    reporter.add("Install state", "Verified", "state file removed (backup retained)")
    return finish(reporter, paths, args)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="install_core.py",
        description="Install or uninstall the standalone Arcanum runtime into ARCANUM_HOME.",
    )
    parser.add_argument("command", choices=["install", "uninstall"])
    parser.add_argument("--check", action="store_true", help="preflight/dry-run only; no filesystem changes")
    parser.add_argument("--non-interactive", action="store_true", help="never prompt; report auth/manual steps instead")
    parser.add_argument(
        "--python",
        help=(
            "absolute path to the interpreter bootstrapped by install.sh/install.ps1 "
            "(default: ARCANUM_BOOTSTRAP_PYTHON, then the newest host Python >=3.12)"
        ),
    )
    parser.add_argument("--prefix", help="base directory for bin/ and tools/ (default: ARCANUM_HOME)")
    parser.add_argument("--home", help="override ARCANUM_HOME (default: env ARCANUM_HOME or ~/.arcanum)")
    parser.add_argument("--repo", help="override repository root (default: parent of installers/)")
    parser.add_argument("--opencode-config", help="override OpenCode config directory")
    parser.add_argument("--maestro-config", help="override Maestro config directory")
    parser.add_argument("--data-dir", help="override runtime data directory (default: <home>/data)")
    parser.add_argument("--skip-downloads", action="store_true", help="do not download pinned binaries")
    parser.add_argument("--skip-tests", action="store_true", help="do not run the pytest suite")
    parser.add_argument("--no-start-dashboard", action="store_true", help="do not launch the Dashboard server")
    parser.add_argument("--timeout", type=int, default=900, help="per-download timeout in seconds (default: 900)")
    return parser.parse_args(argv)


def build_paths(args: argparse.Namespace) -> Paths:
    here = Path(__file__).resolve()
    default_repo = here.parent.parent.parent
    repo = Path(args.repo).expanduser().resolve() if args.repo else default_repo
    home_raw = args.home or os.environ.get("ARCANUM_HOME") or str(Path.home() / ".arcanum")
    home = Path(home_raw).expanduser().resolve()
    prefix = Path(args.prefix).expanduser().resolve() if args.prefix else home
    return Paths(
        repo=repo,
        home=home,
        prefix=prefix,
        opencode_config=Path(args.opencode_config).expanduser().resolve() if args.opencode_config else default_config_dir("opencode"),
        maestro_config=Path(args.maestro_config).expanduser().resolve() if args.maestro_config else default_config_dir("maestro"),
        data_dir=Path(args.data_dir).expanduser().resolve() if args.data_dir else home / "data",
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    reporter = Reporter()
    paths = build_paths(args)
    print(f"Arcanum installer engine {ENGINE_VERSION} | {platform_key()} | repo={paths.repo} | home={paths.home}")
    manifest = load_manifest(paths.repo, reporter)
    if not manifest:
        reporter.print_matrix()
        return 2
    try:
        if args.command == "install":
            return cmd_install(paths, manifest, reporter, args)
        return cmd_uninstall(paths, manifest, reporter, args)
    except KeyboardInterrupt:
        reporter.add("Installer", "Failed", "interrupted by user")
        reporter.print_matrix()
        return 130
    except Exception as exc:  # pragma: no cover - defensive top level
        reporter.add("Installer", "Failed", f"unexpected error: {type(exc).__name__}: {exc}")
        reporter.print_matrix()
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

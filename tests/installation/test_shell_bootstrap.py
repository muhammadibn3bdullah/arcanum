"""Static and behavioural checks for the bash Python bootstrap.

The bootstrap must stay standard-library-only on the shell side: it may use
curl/wget, tar, awk and sha256sum/shasum, but it must never pipe a download into
a shell, and ``--check`` must never download, extract or write anything.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = ROOT / "installers" / "install.sh"
COMMON_SH = ROOT / "installers" / "bootstrap" / "common.sh"
MANIFEST = ROOT / "config" / "dependencies.json"


def run_bash(args: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None):
    return subprocess.run(
        ["bash", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
    )


def function_body(text: str, name: str) -> str:
    """Return the body of a top-level shell function, without its braces."""
    match = re.search(rf"^{re.escape(name)}\(\) \{{\n(.*?)^\}}\n", text, re.S | re.M)
    assert match, f"function {name} not found"
    return match.group(1)


def make_fresh_repo(tmp_path: Path) -> Path:
    """Copy only the installer surface so no repository .venv can be found."""
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    shutil.copytree(ROOT / "installers", repo / "installers")
    shutil.copy2(MANIFEST, repo / "config" / "dependencies.json")
    return repo


def test_shell_scripts_are_syntactically_valid():
    assert COMMON_SH.exists(), "installers/bootstrap/common.sh is missing"
    result = run_bash(["-n", str(INSTALL_SH), str(COMMON_SH)])
    assert result.returncode == 0, result.stderr


def test_shell_scripts_never_pipe_downloads_into_a_shell():
    pipe_to_shell = re.compile(r"(?:curl|wget)[^\n|]*\|\s*(?:/bin/)?(?:ba|z)?sh\b")
    for path in (INSTALL_SH, COMMON_SH):
        text = path.read_text(encoding="utf-8")
        assert not pipe_to_shell.search(text), f"{path} pipes a download into a shell"


def test_check_branch_calls_only_the_write_free_helper():
    text = INSTALL_SH.read_text(encoding="utf-8")
    match = re.search(r"^if \(\( CHECK_ONLY \)\); then\n(.*?)^fi\n", text, re.S | re.M)
    assert match, "install.sh must have an explicit check-only branch"
    branch = match.group(1)
    assert "arc_write_free_check" in branch
    for forbidden in ("arc_bootstrap_python", "arc_download", "arc_extract", "curl", "wget", "tar "):
        assert forbidden not in branch, f"--check branch must not reference {forbidden}"


def test_write_free_check_helper_never_downloads_extracts_or_writes():
    body = function_body(COMMON_SH.read_text(encoding="utf-8"), "arc_write_free_check")
    for forbidden in ("curl", "wget", "tar ", "mkdir", "rm -", "mv ", "arc_download", "arc_extract"):
        assert forbidden not in body, f"arc_write_free_check must not reference {forbidden}"


def test_check_without_python_reports_manual_action_and_writes_nothing(tmp_path):
    repo = make_fresh_repo(tmp_path)
    sandbox_bin = tmp_path / "bin"
    sandbox_bin.mkdir()
    for name in ("bash", "dirname", "uname", "awk"):
        target = shutil.which(name)
        assert target, f"{name} is required to run this test"
        (sandbox_bin / name).symlink_to(target)

    home = tmp_path / "home"
    arc_home = tmp_path / "arcanum-home"
    env = {
        "PATH": str(sandbox_bin),
        "HOME": str(home),
        "ARCANUM_HOME": str(arc_home),
    }

    result = subprocess.run(
        ["bash", str(repo / "installers" / "install.sh"), "--check", "--non-interactive"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "Needs manual action" in combined, combined
    assert "Python bootstrap" in combined, combined
    assert not arc_home.exists(), "--check created ARCANUM_HOME"
    assert not home.exists() or list(home.iterdir()) == [], "--check wrote under HOME"


def test_manifest_field_extraction_preserves_literal_plus():
    url = run_bash(
        [
            "-c",
            f'source "{COMMON_SH}"; arc_manifest_field "{MANIFEST}" linux-x64 url',
        ]
    )
    assert url.returncode == 0, url.stderr
    value = url.stdout.strip()
    assert "+" in value, value
    assert "%2B" not in value, value
    assert " " not in value, value
    assert value.endswith("cpython-3.12.16+20261124-x86_64-unknown-linux-gnu-install_only.tar.gz")

    size = run_bash(
        [
            "-c",
            f'source "{COMMON_SH}"; arc_manifest_field "{MANIFEST}" linux-x64 size',
        ]
    )
    assert size.returncode == 0, size.stderr
    assert size.stdout.strip().isdigit(), size.stdout


def test_asset_key_mapping_from_uname(tmp_path):
    sandbox_bin = tmp_path / "bin"
    sandbox_bin.mkdir()
    uname = sandbox_bin / "uname"
    uname.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        "  -s) echo Darwin ;;\n"
        "  -m) echo arm64 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    uname.chmod(0o755)

    result = subprocess.run(
        ["bash", "-c", f'source "{COMMON_SH}"; arc_platform_asset_key'],
        capture_output=True,
        text=True,
        env={"PATH": f"{sandbox_bin}:/usr/bin:/bin"},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "darwin-arm64"


def test_sha256_verification_accepts_and_rejects(tmp_path):
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"arcanum-bootstrap-test")
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()

    ok = run_bash(["-c", f'source "{COMMON_SH}"; arc_verify_sha256 "{payload}" "{digest}"'])
    assert ok.returncode == 0, ok.stderr

    wrong = "0" * 64
    bad = run_bash(["-c", f'source "{COMMON_SH}"; arc_verify_sha256 "{payload}" "{wrong}"'])
    assert bad.returncode != 0


def make_python_archive(path: Path) -> None:
    payload = b"#!/bin/sh\nexit 0\n"
    info = tarfile.TarInfo("python/bin/python3")
    info.size = len(payload)
    info.mode = 0o755
    with tarfile.open(path, "w:gz") as archive:
        archive.addfile(info, io.BytesIO(payload))


def write_bootstrap_manifest(repo: Path, source: Path, *, sha256: str | None = None) -> None:
    payload = source.read_bytes()
    manifest = {
        "python": {
            "bootstrap": {
                "assets": {
                    "test-x64": {
                        "asset": "cpython-3.12.16+20261124-test.tar.gz",
                        "url": source.as_uri(),
                        "sha256": sha256 or hashlib.sha256(payload).hexdigest(),
                        "size": len(payload),
                        "python_path": "python/bin/python3",
                    }
                }
            }
        }
    }
    (repo / "config").mkdir(parents=True, exist_ok=True)
    (repo / "config" / "dependencies.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


def test_bootstrap_downloads_verifies_and_extracts_offline(tmp_path):
    source = tmp_path / "source.tar.gz"
    make_python_archive(source)
    repo = tmp_path / "repo"
    write_bootstrap_manifest(repo, source)
    home = tmp_path / "arcanum-home"

    result = run_bash(
        ["-c", f'source "{COMMON_SH}"; arc_bootstrap_python "{home}" "{repo}" test-x64']
    )

    assert result.returncode == 0, result.stderr
    interpreter = Path(result.stdout.strip())
    assert interpreter.is_absolute()
    assert interpreter == home / "runtime" / "python" / "bin" / "python3"
    assert interpreter.exists()
    assert interpreter.stat().st_mode & 0o111

    cached = home / "cache" / "python" / "cpython-3.12.16+20261124-test.tar.gz"
    assert cached.exists(), "archive with a literal '+' must be cached verbatim"
    assert cached.read_bytes() == source.read_bytes()


def test_bootstrap_rejects_checksum_mismatch_before_extracting(tmp_path):
    source = tmp_path / "source.tar.gz"
    make_python_archive(source)
    repo = tmp_path / "repo"
    write_bootstrap_manifest(repo, source, sha256="0" * 64)
    home = tmp_path / "arcanum-home"

    result = run_bash(
        ["-c", f'source "{COMMON_SH}"; arc_bootstrap_python "{home}" "{repo}" test-x64']
    )

    assert result.returncode != 0
    assert "SHA-256 mismatch" in result.stderr
    assert not (home / "runtime" / "python").exists()
    assert not (home / "cache" / "python" / "cpython-3.12.16+20261124-test.tar.gz").exists()


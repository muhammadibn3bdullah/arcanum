#!/usr/bin/env bash
# Arcanum standalone installer (macOS / Linux entry point).
#
# Zero-prerequisite flow:
#   1. use an existing Python >=3.12 (explicit ARCANUM_PYTHON, an existing
#      venv, or python3 on PATH);
#   2. otherwise bootstrap a pinned, checksum-verified standalone CPython
#      under ARCANUM_HOME/runtime;
#   3. exec installers/modules/install_core.py with that interpreter.
#
# `--check` stays strictly write-free: with no suitable Python it reports
# "Needs manual action" and exits without downloading, extracting or writing.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
CORE="$SCRIPT_DIR/modules/install_core.py"
# shellcheck source=installers/bootstrap/common.sh
source "$SCRIPT_DIR/bootstrap/common.sh"

CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=1 ;;
  esac
done

ARCANUM_HOME_DIR="${ARCANUM_HOME:-$HOME/.arcanum}"

PYTHON_BIN=""
if [[ -n "${ARCANUM_PYTHON:-}" ]]; then
  if arc_python_version_ok "$ARCANUM_PYTHON"; then
    PYTHON_BIN="$ARCANUM_PYTHON"
  else
    echo "ERROR: ARCANUM_PYTHON=$ARCANUM_PYTHON is not an executable Python >=${ARC_PYTHON_MIN_MAJOR}.${ARC_PYTHON_MIN_MINOR}." >&2
    exit 127
  fi
fi

if [[ -z "$PYTHON_BIN" ]]; then
  for candidate in \
    "$ARCANUM_HOME_DIR/venv/bin/python" \
    "$REPO_ROOT/.venv/bin/python" \
    "$(command -v python3 || true)" \
    "$(command -v python || true)" \
    "$ARCANUM_HOME_DIR/runtime/python/bin/python3"
  do
    if arc_python_version_ok "$candidate"; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi

if (( CHECK_ONLY )); then
  if [[ -n "$PYTHON_BIN" ]]; then
    exec "$PYTHON_BIN" "$CORE" install --repo "$REPO_ROOT" "$@"
  fi
  ASSET_KEY="$(arc_platform_asset_key)" || exit 2
  arc_write_free_check "$ARCANUM_HOME_DIR" "$REPO_ROOT" "$ASSET_KEY"
  echo "NOTE: downstream stages were not checked because no Python >=${ARC_PYTHON_MIN_MAJOR}.${ARC_PYTHON_MIN_MINOR} is available yet." >&2
  exit 0
fi

if [[ -z "$PYTHON_BIN" ]]; then
  ASSET_KEY="$(arc_platform_asset_key)" || exit 2
  PYTHON_BIN="$(arc_bootstrap_python "$ARCANUM_HOME_DIR" "$REPO_ROOT" "$ASSET_KEY")"
  export ARCANUM_BOOTSTRAP_PYTHON="$PYTHON_BIN"
  exec "$PYTHON_BIN" "$CORE" install --repo "$REPO_ROOT" --python "$PYTHON_BIN" "$@"
fi

exec "$PYTHON_BIN" "$CORE" install --repo "$REPO_ROOT" "$@"

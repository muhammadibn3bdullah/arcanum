#!/usr/bin/env bash
# Arcanum standalone installer (macOS / Linux entry point).
# Thin wrapper: all work is done by modules/install_core.py.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
CORE="$SCRIPT_DIR/modules/install_core.py"

PYTHON_BIN="${ARCANUM_PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  ARCANUM_HOME_DIR="${ARCANUM_HOME:-$HOME/.arcanum}"
  for candidate in \
    "$ARCANUM_HOME_DIR/venv/bin/python" \
    "$REPO_ROOT/.venv/bin/python" \
    "$(command -v python3 || true)" \
    "$(command -v python || true)"
  do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "ERROR: python3 not found. Set ARCANUM_PYTHON=/path/to/python3." >&2
  exit 127
fi

exec "$PYTHON_BIN" "$CORE" install --repo "$REPO_ROOT" "$@"

#!/usr/bin/env bash
# Arcanum bash bootstrap helpers (sourced by installers/install.sh).
#
# Purpose: when the host has no Python >=3.12, download one pinned standalone
# CPython archive from config/dependencies.json, verify its SHA-256, extract it
# under <ARCANUM_HOME>/runtime, verify the interpreter, and print its absolute
# path. Everything else is left to installers/modules/install_core.py.
#
# Host tooling contract (OS-level, not a Python prerequisite):
#   - curl or wget
#   - tar (gzip support)
#   - awk
#   - wc
#   - sha256sum or shasum
# If any is missing, callers get "UNSUPPORTED" on stderr and a non-zero status.
#
# Security rules:
#   - Never pipe a download into a shell.
#   - Never extract an archive before its pinned SHA-256 has been verified.
#   - The pinned URL contains a literal '+' in the asset name. It is fetched
#     exactly as stored in config/dependencies.json; never rewrite '+' to
#     '%2B' and never let a client decode '+' as a space.
#
# This file only defines functions and constants; sourcing it has no side
# effects and writes nothing.

# Minimum project interpreter, matching pyproject.toml requires-python.
ARC_PYTHON_MIN_MAJOR=3
ARC_PYTHON_MIN_MINOR=12

# Print an absolute path without requiring the path to exist.
arc_abspath() {
  local path="$1"
  case "$path" in
    /*) printf '%s\n' "$path" ;;
    *) printf '%s/%s\n' "$PWD" "$path" ;;
  esac
}

# Read a scalar field from the bootstrap asset block in dependencies.json.
# Usage: arc_manifest_field <manifest> <asset-key> <field>
arc_manifest_field() {
  local manifest="$1" key="$2" field="$3"
  if [[ ! -r "$manifest" ]]; then
    echo "ERROR: dependency manifest is not readable: $manifest" >&2
    return 1
  fi
  awk -v key="$key" -v field="$field" '
    BEGIN { inblock = 0 }
    !inblock && $0 ~ ("\"" key "\"[[:space:]]*:") { inblock = 1; next }
    inblock && $0 ~ ("\"" field "\"[[:space:]]*:") {
      line = $0
      sub(/^[^:]*:[[:space:]]*/, "", line)
      if (substr(line, 1, 1) == "\"") {
        sub(/^"/, "", line)
        sub(/".*$/, "", line)
      } else {
        sub(/[,[:space:]].*$/, "", line)
      }
      print line
      exit
    }
    inblock && $0 ~ /^[[:space:]]*}/ { exit }
  ' "$manifest"
}

# Map the host OS/architecture to a manifest asset key.
arc_platform_asset_key() {
  local os arch
  os="$(uname -s 2>/dev/null)" || {
    echo "UNSUPPORTED: uname is required to detect the host platform" >&2
    return 1
  }
  arch="$(uname -m 2>/dev/null)" || {
    echo "UNSUPPORTED: uname is required to detect the host architecture" >&2
    return 1
  }

  case "$os" in
    Linux) os="linux" ;;
    Darwin) os="darwin" ;;
    *)
      echo "UNSUPPORTED: the Python bootstrap does not support OS '$os'" >&2
      return 1
      ;;
  esac

  case "$arch" in
    x86_64|amd64) arch="x64" ;;
    arm64|aarch64) arch="arm64" ;;
    *)
      echo "UNSUPPORTED: the Python bootstrap does not support architecture '$arch'" >&2
      return 1
      ;;
  esac

  printf '%s-%s\n' "$os" "$arch"
}

# Fail fast when the host lacks the shell-level tools the bootstrap needs.
arc_require_tools() {
  local missing=""
  if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    missing="$missing curl-or-wget"
  fi
  command -v tar >/dev/null 2>&1 || missing="$missing tar"
  command -v awk >/dev/null 2>&1 || missing="$missing awk"
  command -v wc >/dev/null 2>&1 || missing="$missing wc"
  if ! command -v sha256sum >/dev/null 2>&1 && ! command -v shasum >/dev/null 2>&1; then
    missing="$missing sha256sum-or-shasum"
  fi
  if [[ -n "$missing" ]]; then
    echo "UNSUPPORTED: missing required host tools:$missing" >&2
    return 1
  fi
  return 0
}

arc_sha256_file() {
  local file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file" | awk '{print $1}'
  else
    echo "UNSUPPORTED: neither sha256sum nor shasum is available" >&2
    return 1
  fi
}

arc_verify_sha256() {
  local file="$1" expected="$2" actual
  actual="$(arc_sha256_file "$file")" || return 1
  [[ "$actual" == "$expected" ]]
}

# Compressed archive byte count. The manifest "size" field is Content-Length,
# not the extracted tree size.
arc_file_size() {
  local file="$1" bytes
  bytes="$(wc -c < "$file")" || return 1
  printf '%s\n' "$bytes" | awk '{print $1}'
}

arc_python_version_ok() {
  local candidate="$1"
  [[ -n "$candidate" && -x "$candidate" ]] || return 1
  "$candidate" -c "import sys; raise SystemExit(0 if sys.version_info >= (${ARC_PYTHON_MIN_MAJOR}, ${ARC_PYTHON_MIN_MINOR}) else 1)" \
    >/dev/null 2>&1
}

# Download a pinned URL to dest. The URL is passed through verbatim; see the
# '+' rule in the file header.
arc_download() {
  local url="$1" dest="$2" partial="$2.part"
  rm -f "$partial"
  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --retry 3 --connect-timeout 30 --output "$partial" "$url" >&2
  elif command -v wget >/dev/null 2>&1; then
    wget --tries=3 --timeout=30 --output-document="$partial" "$url" >&2
  else
    echo "UNSUPPORTED: curl or wget is required to download $url" >&2
    return 1
  fi
  mv -f "$partial" "$dest"
}

# Download, verify, extract and validate the pinned interpreter.
# Usage: arc_bootstrap_python <home> <repo> <asset-key>
# Prints only the absolute interpreter path on stdout; diagnostics go to stderr.
arc_bootstrap_python() {
  local home repo key manifest asset url expected_sha expected_size python_path
  local archive runtime root py actual actual_size

  home="$(arc_abspath "$1")"
  repo="$(arc_abspath "$2")"
  key="$3"
  manifest="$repo/config/dependencies.json"

  arc_require_tools || return 1

  asset="$(arc_manifest_field "$manifest" "$key" asset)" || return 1
  url="$(arc_manifest_field "$manifest" "$key" url)" || return 1
  expected_sha="$(arc_manifest_field "$manifest" "$key" sha256)" || return 1
  expected_size="$(arc_manifest_field "$manifest" "$key" size)" || return 1
  python_path="$(arc_manifest_field "$manifest" "$key" python_path)" || return 1

  if [[ -z "$asset" || -z "$url" || -z "$expected_sha" || -z "$python_path" ]]; then
    echo "ERROR: python.bootstrap.$key is missing asset/url/sha256/python_path in $manifest" >&2
    return 1
  fi
  if [[ "$asset" == */* ]]; then
    echo "ERROR: refusing asset name containing a path separator: $asset" >&2
    return 1
  fi

  archive="$home/cache/python/$asset"
  runtime="$home/runtime"
  mkdir -p "$home/cache/python" "$runtime"

  if [[ -f "$archive" ]] && arc_verify_sha256 "$archive" "$expected_sha"; then
    echo "  -> using verified cached archive: $archive" >&2
  else
    rm -f "$archive"
    echo "  -> downloading pinned CPython: $asset" >&2
    arc_download "$url" "$archive" || return 1
    if ! arc_verify_sha256 "$archive" "$expected_sha"; then
      actual="$(arc_sha256_file "$archive" 2>/dev/null || true)"
      rm -f "$archive"
      echo "ERROR: SHA-256 mismatch for $asset (expected $expected_sha, got ${actual:-unknown})" >&2
      return 1
    fi
    echo "  -> SHA-256 verified: $expected_sha" >&2
  fi

  if [[ -n "$expected_size" ]]; then
    actual_size="$(arc_file_size "$archive")" || return 1
    if [[ "$actual_size" != "$expected_size" ]]; then
      rm -f "$archive"
      echo "ERROR: archive size mismatch for $asset (expected $expected_size bytes, got $actual_size)" >&2
      return 1
    fi
  fi

  root="${python_path%%/*}"
  echo "  -> extracting into $runtime" >&2
  rm -rf "$runtime/$root"
  tar -xzf "$archive" -C "$runtime" || return 1

  py="$runtime/$python_path"
  if ! arc_python_version_ok "$py"; then
    echo "ERROR: bootstrapped interpreter failed the Python >=${ARC_PYTHON_MIN_MAJOR}.${ARC_PYTHON_MIN_MINOR} check: $py" >&2
    return 1
  fi

  printf '%s\n' "$py"
}

# Write-free reporting for install.sh --check. Detects an already bootstrapped
# runtime or reports what a real run would need. It never downloads, extracts,
# creates directories or writes files.
# Usage: arc_write_free_check <home> <repo> <asset-key>
arc_write_free_check() {
  local home repo key manifest python_path asset py version
  home="$1"
  repo="$2"
  key="$3"
  manifest="$repo/config/dependencies.json"

  python_path="$(arc_manifest_field "$manifest" "$key" python_path 2>/dev/null || true)"
  asset="$(arc_manifest_field "$manifest" "$key" asset 2>/dev/null || true)"

  if [[ -n "$python_path" ]]; then
    py="$home/runtime/$python_path"
    if arc_python_version_ok "$py"; then
      version="$("$py" --version 2>&1)"
      printf '[%20s] Python bootstrap: %s (%s)\n' "Verified" "$py" "$version"
      return 0
    fi
  fi

  printf '[%20s] Python bootstrap: no host Python >=%s.%s; re-run without --check to download pinned CPython (%s)\n' \
    "Needs manual action" "$ARC_PYTHON_MIN_MAJOR" "$ARC_PYTHON_MIN_MINOR" "${asset:-$key}"
  return 0
}

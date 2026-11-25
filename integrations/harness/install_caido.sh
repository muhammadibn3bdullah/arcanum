#!/usr/bin/env bash
# Local Caido installer (no root required).
# Download the Linux x86_64 tar.gz or AppImage yourself from:
#   https://github.com/caido/caido/releases/latest
# then run:  bash install_caido.sh /path/to/downloaded/file
set -euo pipefail

FILE="${1:-}"
if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
  echo "Usage: $0 /path/to/caido-desktop-vX-linux-x86_64.{tar.gz,AppImage}"
  exit 1
fi

mkdir -p ~/.local/bin ~/.local/share/caido

case "$FILE" in
  *.AppImage|*.appimage)
    cp "$FILE" ~/.local/bin/caido
    chmod +x ~/.local/bin/caido
    ;;
  *.tar.gz|*.tgz)
    rm -rf ~/.local/share/caido/*
    tar xzf "$FILE" -C ~/.local/share/caido
    BIN=$(find ~/.local/share/caido -maxdepth 3 -type f -name 'caido*' -perm -u+x | head -1)
    [ -n "$BIN" ] || { echo "ERROR: no executable found in archive"; exit 1; }
    ln -sf "$BIN" ~/.local/bin/caido
    ;;
  *)
    echo "ERROR: expected .AppImage or .tar.gz"
    exit 1
    ;;
esac

echo "Installed: $(readlink -f ~/.local/bin/caido)"
echo "Next: run 'caido' and open http://127.0.0.1:8080"
echo "Then browse an authorized target through Caido so api-fingerprint-caido has traffic."

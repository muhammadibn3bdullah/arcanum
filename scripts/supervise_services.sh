#!/usr/bin/env bash
# Keep Burp MCP and Caido alive. Runs as a user systemd service.
set -uo pipefail

SCRIPT="${HOME}/Projects/arcanum/scripts/start_services.sh"
LOG_DIR="${HOME}/.local/share/arcanum-logs"
mkdir -p "$LOG_DIR"

burp_up() {
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:9876/ 2>/dev/null || true)
  [ "$code" = "200" ]
}

caido_up() {
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:8080/ 2>/dev/null || true)
  [ "$code" = "200" ]
}

echo "supervisor: starting"
"$SCRIPT" || echo "supervisor: initial start failed; will retry"

while true; do
  sleep 15
  if ! burp_up || ! caido_up; then
    echo "supervisor: $(date -Is) service down (burp=$(burp_up && echo up || echo down) caido=$(caido_up && echo up || echo down)); restarting"
    "$SCRIPT" >>"$LOG_DIR/supervisor.log" 2>&1 || true
  fi
done

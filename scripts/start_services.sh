#!/usr/bin/env bash
# Start Arcanum's external services in TRUE HEADLESS mode (no GUI/session needed).
set -euo pipefail

BURP_JAR="${HOME}/BurpSuite/burpsuite.jar"
BURP_CONFIG="${HOME}/.BurpSuite/arcanum-user-options.json"
CAIDO_CLI="${HOME}/.local/bin/caido-cli"
JAVA="${HOME}/BurpSuite/jre/bin/java"
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

start_burp() {
  if burp_up; then
    echo "burp-mcp: already listening on 9876"
    return
  fi
  echo "burp-mcp: starting (headless)"
  nohup "$JAVA" -Djava.awt.headless=true -XX:MaxRAMPercentage=25 \
    -jar "$BURP_JAR" --user-config-file="$BURP_CONFIG" \
    >"$LOG_DIR/burp-headless.log" 2>&1 < /dev/null &
}

start_caido() {
  if caido_up; then
    echo "caido: already listening on 8080"
    return
  fi
  echo "caido: starting (headless caido-cli)"
  nohup "$CAIDO_CLI" --listen 127.0.0.1:8080 --no-open --no-sync --allow-guests \
    >"$LOG_DIR/caido-cli.log" 2>&1 < /dev/null &
}

start_burp
start_caido
echo "Waiting for services..."
for i in $(seq 1 45); do
  b=0; c=0
  burp_up && b=1
  caido_up && c=1
  if [ "$b" = 1 ] && [ "$c" = 1 ]; then
    echo "READY burp=up caido=up"
    exit 0
  fi
  sleep 2
done
echo "TIMEOUT burp=$b caido=$c logs=$LOG_DIR" >&2
exit 1

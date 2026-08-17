#!/usr/bin/env bash
set -euo pipefail

RUNTIME_DIR="${A2A_RUNTIME_DIR:-/home/dell/.local/a2a-runtime}"
JAVA_HOME="${JAVA_HOME:-$RUNTIME_DIR/jdk}"
NACOS_HOME="${NACOS_HOME:-$RUNTIME_DIR/nacos}"
PID_FILE="$NACOS_HOME/logs/a2a-nacos.pid"

export JAVA_HOME
export PATH="$JAVA_HOME/bin:$PATH"

if [ -f "$PID_FILE" ]; then
  pid="$(cat "$PID_FILE")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    rm -f "$PID_FILE"
    echo "Stopped Nacos pid $pid"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

if [ -x "$NACOS_HOME/bin/shutdown.sh" ]; then
  cd "$NACOS_HOME"
  exec "$NACOS_HOME/bin/shutdown.sh"
fi

echo "Nacos is not running"

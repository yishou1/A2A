#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_ROOT/logs/decision_remote_stack"

for name in auth_mock track_threat_agent decision_planning_agent compliance_authorization_agent; do
  pid_file="$LOG_DIR/$name.pid"
  if [ ! -f "$pid_file" ]; then
    echo "$name is not running"
    continue
  fi

  pid="$(cat "$pid_file")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    echo "Stopped $name pid $pid"
  else
    echo "$name pid $pid is not alive"
  fi
  rm -f "$pid_file"
done

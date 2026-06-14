#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_ROOT/logs/decision_remote_stack"

mkdir -p "$LOG_DIR"

if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
  PYTHON_EXEC="$PROJECT_ROOT/.venv/bin/python"
elif [ -x "$PROJECT_ROOT/venv/bin/python" ]; then
  PYTHON_EXEC="$PROJECT_ROOT/venv/bin/python"
else
  PYTHON_EXEC="${PYTHON_EXEC:-python3}"
fi

if [ -f "$PROJECT_ROOT/.env" ]; then
  set -a
  source "$PROJECT_ROOT/.env"
  set +a
fi

export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"
export NACOS_ADDR="${NACOS_ADDR:-127.0.0.1:8848}"
export NACOS_NAMESPACE="${NACOS_NAMESPACE:-public}"
export A2A_AUTH_SERVER_BASE="${A2A_AUTH_SERVER_BASE:-http://127.0.0.1:8080}"
export A2A_HEARTBEAT_INTERVAL="${A2A_HEARTBEAT_INTERVAL:-5}"
export DECISION_PLANNING_AGENT_PORT="${DECISION_PLANNING_AGENT_PORT:-10202}"
export COMPLIANCE_AUTHORIZATION_AGENT_PORT="${COMPLIANCE_AUTHORIZATION_AGENT_PORT:-10203}"

start_process() {
  local name="$1"
  local command="$2"
  local pid_file="$LOG_DIR/$name.pid"
  local log_file="$LOG_DIR/$name.log"

  if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    echo "$name already running with pid $(cat "$pid_file")"
    return
  fi

  cd "$PROJECT_ROOT"
  setsid bash -lc "$command" > "$log_file" 2>&1 < /dev/null &
  echo "$!" > "$pid_file"
  echo "Started $name pid $(cat "$pid_file"). Log: $log_file"
}

start_process "auth_mock" "$PYTHON_EXEC -u scripts/auth_mock_server.py"
start_process "decision_planning_agent" "$PYTHON_EXEC -u decision_planning_agent/main.py"
start_process "compliance_authorization_agent" "$PYTHON_EXEC -u compliance_authorization_agent/main.py"

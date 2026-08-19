#!/bin/bash
RAW_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$RAW_SCRIPT_DIR"
PYTHONPATH_DIR="$RAW_SCRIPT_DIR"
PYTHONPATH_SEP=":"

if command -v cygpath >/dev/null 2>&1; then
    SCRIPT_DIR="$(cygpath -w "$RAW_SCRIPT_DIR")"
    PYTHONPATH_DIR="$SCRIPT_DIR"
    PYTHONPATH_SEP=";"
fi

if [ -n "$PYTHONPATH" ]; then
    export PYTHONPATH="$PYTHONPATH_DIR$PYTHONPATH_SEP$PYTHONPATH"
else
    export PYTHONPATH="$PYTHONPATH_DIR"
fi

if [ -x "$SCRIPT_DIR/venv/bin/python" ]; then
    PYTHON_EXEC="$SCRIPT_DIR/venv/bin/python"
elif [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
    PYTHON_EXEC="$SCRIPT_DIR/.venv/bin/python"
else
    PYTHON_EXEC="${PYTHON_EXEC:-python3}"
fi

OVERRIDE_PYTHON_EXEC="${PYTHON_EXEC-}"
OVERRIDE_NACOS_ADDR="${NACOS_ADDR-}"
OVERRIDE_NACOS_NAMESPACE="${NACOS_NAMESPACE-}"
OVERRIDE_A2A_AUTH_SERVER_BASE="${A2A_AUTH_SERVER_BASE-}"
OVERRIDE_A2A_COMMANDER_MODE="${A2A_COMMANDER_MODE-}"

if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

if [ -n "$OVERRIDE_PYTHON_EXEC" ]; then
    export PYTHON_EXEC="$OVERRIDE_PYTHON_EXEC"
fi
if [ -n "$OVERRIDE_NACOS_ADDR" ]; then
    export NACOS_ADDR="$OVERRIDE_NACOS_ADDR"
fi
if [ -n "$OVERRIDE_NACOS_NAMESPACE" ]; then
    export NACOS_NAMESPACE="$OVERRIDE_NACOS_NAMESPACE"
fi
if [ -n "$OVERRIDE_A2A_AUTH_SERVER_BASE" ]; then
    export A2A_AUTH_SERVER_BASE="$OVERRIDE_A2A_AUTH_SERVER_BASE"
fi
if [ -n "$OVERRIDE_A2A_COMMANDER_MODE" ]; then
    export A2A_COMMANDER_MODE="$OVERRIDE_A2A_COMMANDER_MODE"
fi

export TACTICAL_INTELLIGENCE_AGENT_PORT="${TACTICAL_INTELLIGENCE_AGENT_PORT:-10200}"
export TIA_PORT="${TIA_PORT:-$TACTICAL_INTELLIGENCE_AGENT_PORT}"
export TIA_NACOS_REGISTER="${TIA_NACOS_REGISTER:-1}"
export TIA_EXECUTION_MODE="${TIA_EXECUTION_MODE:-in_process}"
export TIA_USE_MOCK="${TIA_USE_MOCK:-1}"
export TRACK_THREAT_AGENT_PORT="${TRACK_THREAT_AGENT_PORT:-8018}"
export TRACK_THREAT_AGENT_HOST="${TRACK_THREAT_AGENT_HOST:-127.0.0.1}"
export NACOS_ADDR="${NACOS_ADDR:-127.0.0.1:8848}"
export NACOS_NAMESPACE="${NACOS_NAMESPACE:-public}"
export NACOS_SERVER="${NACOS_SERVER:-$NACOS_ADDR}"
export A2A_AUTH_SERVER_BASE="${A2A_AUTH_SERVER_BASE:-http://127.0.0.1:8080}"
export A2A_COMMANDER_MODE="${A2A_COMMANDER_MODE:-remote}"
export A2A_REQUEST_TIMEOUT="${A2A_REQUEST_TIMEOUT:-60}"
export TASK_SCHEDULING_AGENT_PORT="${TASK_SCHEDULING_AGENT_PORT:-10201}"
export EXECUTION_CONTROL_AGENT_PORT="${EXECUTION_CONTROL_AGENT_PORT:-8017}"
export SIMULATION_EXECUTION_AGENT_PORT="${SIMULATION_EXECUTION_AGENT_PORT:-10204}"
export CLOSED_LOOP_AGENT_PORT="${CLOSED_LOOP_AGENT_PORT:-8016}"
export DECISION_PLANNING_AGENT_PORT="${DECISION_PLANNING_AGENT_PORT:-10202}"
export COMPLIANCE_AUTHORIZATION_AGENT_PORT="${COMPLIANCE_AUTHORIZATION_AGENT_PORT:-10203}"

echo "Starting Tactical Intelligence Agent..."
"$PYTHON_EXEC" "$SCRIPT_DIR/tactical_intelligence_agent/main.py" &
sleep 2

echo "Starting Track Threat Agent..."
SERVICE_IP="$TRACK_THREAT_AGENT_HOST" \
SERVICE_PORT="$TRACK_THREAT_AGENT_PORT" \
AGENT_ROLE=track_threat \
NACOS_ENABLED=true \
"$PYTHON_EXEC" -m uvicorn track_threat_agent.app.main:app --host "$TRACK_THREAT_AGENT_HOST" --port "$TRACK_THREAT_AGENT_PORT" &
sleep 2

echo "Starting Task Scheduling Agent..."
"$PYTHON_EXEC" -m task_scheduling_agent.main &
sleep 2

echo "Starting Execution Control Agent..."
"$PYTHON_EXEC" "$SCRIPT_DIR/execution_control_agent/main.py" &
sleep 2

echo "Starting Simulation Execution Agent..."
EXECUTION_CONTROL_AGENT_ROLE=simulation_execution \
SIMULATION_EXECUTION_AGENT_PORT="$SIMULATION_EXECUTION_AGENT_PORT" \
"$PYTHON_EXEC" "$SCRIPT_DIR/execution_control_agent/main.py" &
sleep 2

echo "Starting Closed Loop Agent..."
"$PYTHON_EXEC" "$SCRIPT_DIR/closed_loop_agent/main.py" &
sleep 2

echo "Starting Decision Planning Agent..."
export DECISION_AGENT_BACKEND="${DECISION_AGENT_BACKEND:-algolib}"
export DECISION_AGENT_ALGOLIB_LLM="${DECISION_AGENT_ALGOLIB_LLM:-false}"
"$PYTHON_EXEC" "$SCRIPT_DIR/decision_planning_agent/main.py" &
sleep 2

echo "Starting Compliance Authorization Agent..."
export DECISION_AGENT_BACKEND="${DECISION_AGENT_BACKEND:-algolib}"
export DECISION_AGENT_ALGOLIB_LLM="${DECISION_AGENT_ALGOLIB_LLM:-false}"
"$PYTHON_EXEC" "$SCRIPT_DIR/compliance_authorization_agent/main.py" &
sleep 2

echo "Starting Commander Agent..."
"$PYTHON_EXEC" "$SCRIPT_DIR/commander_agent/main.py" "$@"

#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"

if [ -x "$SCRIPT_DIR/venv/bin/python" ]; then
    PYTHON_EXEC="$SCRIPT_DIR/venv/bin/python"
elif [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
    PYTHON_EXEC="$SCRIPT_DIR/.venv/bin/python"
else
    PYTHON_EXEC="${PYTHON_EXEC:-python3}"
fi

if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

export RECON_AGENT_PORT="${RECON_AGENT_PORT:-8012}"
export TRACK_THREAT_AGENT_PORT="${TRACK_THREAT_AGENT_PORT:-8018}"
export EXECUTION_CONTROL_AGENT_PORT="${EXECUTION_CONTROL_AGENT_PORT:-8017}"
export ARTILLERY_AGENT_PORT="${ARTILLERY_AGENT_PORT:-8013}"
export ASSAULT_AGENT_PORT="${ASSAULT_AGENT_PORT:-8014}"
export EVALUATOR_AGENT_PORT="${EVALUATOR_AGENT_PORT:-8015}"
export CLOSED_LOOP_AGENT_PORT="${CLOSED_LOOP_AGENT_PORT:-8016}"
export DECISION_PLANNING_AGENT_PORT="${DECISION_PLANNING_AGENT_PORT:-10202}"
export COMPLIANCE_AUTHORIZATION_AGENT_PORT="${COMPLIANCE_AUTHORIZATION_AGENT_PORT:-10203}"

echo "Starting Recon Agent..."
"$PYTHON_EXEC" "$SCRIPT_DIR/recon_agent/main.py" &
sleep 2

echo "Starting Track Threat Agent..."
"$PYTHON_EXEC" "$SCRIPT_DIR/track_threat_agent/app/main.py" &
sleep 2

echo "Starting Execution Control Agent..."
"$PYTHON_EXEC" "$SCRIPT_DIR/execution_control_agent/main.py" &
sleep 2

echo "Starting Artillery Agent..."
"$PYTHON_EXEC" "$SCRIPT_DIR/artillery_agent/main.py" &
sleep 2

echo "Starting Assault Agent..."
"$PYTHON_EXEC" "$SCRIPT_DIR/assault_agent/main.py" &
sleep 2

echo "Starting Evaluator Agent..."
"$PYTHON_EXEC" "$SCRIPT_DIR/evaluator_agent/main.py" &
sleep 2

echo "Starting Closed Loop Agent..."
"$PYTHON_EXEC" "$SCRIPT_DIR/closed_loop_agent/main.py" &
sleep 2

echo "Starting Decision Planning Agent..."
"$PYTHON_EXEC" "$SCRIPT_DIR/decision_planning_agent/main.py" &
sleep 2

echo "Starting Compliance Authorization Agent..."
"$PYTHON_EXEC" "$SCRIPT_DIR/compliance_authorization_agent/main.py" &
sleep 2

echo "Starting Commander Agent..."
"$PYTHON_EXEC" "$SCRIPT_DIR/commander_agent/main.py"

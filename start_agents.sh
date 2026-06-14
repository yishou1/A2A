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

export RECON_AGENT_PORT=8012
export ARTILLERY_AGENT_PORT=8013
export ASSAULT_AGENT_PORT=8014
export EVALUATOR_AGENT_PORT=8015
export DECISION_PLANNING_AGENT_PORT=10202
export COMPLIANCE_AUTHORIZATION_AGENT_PORT=10203

echo "Starting Recon Agent..."
$PYTHON_EXEC "$SCRIPT_DIR/recon_agent/main.py" &
sleep 2

echo "Starting Artillery Agent..."
$PYTHON_EXEC "$SCRIPT_DIR/artillery_agent/main.py" &
sleep 2

echo "Starting Assault Agent..."
$PYTHON_EXEC "$SCRIPT_DIR/assault_agent/main.py" &
sleep 2

echo "Starting Evaluator Agent..."
$PYTHON_EXEC "$SCRIPT_DIR/evaluator_agent/main.py" &
sleep 2

echo "Starting Decision Planning Agent..."
$PYTHON_EXEC "$SCRIPT_DIR/decision_planning_agent/main.py" &
sleep 2

echo "Starting Compliance Authorization Agent..."
$PYTHON_EXEC "$SCRIPT_DIR/compliance_authorization_agent/main.py" &
sleep 2

echo "Starting Commander Agent..."
$PYTHON_EXEC "$SCRIPT_DIR/commander_agent/main.py"

#!/bin/bash
export PYTHONPATH=/home/yl/yl/jzz/A2A:$PYTHONPATH
PYTHON_EXEC=/home/yl/yl/jzz/A2A/venv/bin/python

if [ -f /home/yl/yl/jzz/A2A/.env ]; then
    set -a
    source /home/yl/yl/jzz/A2A/.env
    set +a
fi

export RECON_AGENT_PORT=8012
export EXECUTION_CONTROL_AGENT_PORT=8017
export ARTILLERY_AGENT_PORT=8013
export ASSAULT_AGENT_PORT=8014
export EVALUATOR_AGENT_PORT=8015
export CLOSED_LOOP_AGENT_PORT=8016

echo "Starting Recon Agent..."
$PYTHON_EXEC /home/yl/yl/jzz/A2A/recon_agent/main.py &
sleep 2

echo "Starting Execution Control Agent..."
$PYTHON_EXEC /home/yl/yl/jzz/A2A/execution_control_agent/main.py &
sleep 2

echo "Starting Artillery Agent..."
$PYTHON_EXEC /home/yl/yl/jzz/A2A/artillery_agent/main.py &
sleep 2

echo "Starting Assault Agent..."
$PYTHON_EXEC /home/yl/yl/jzz/A2A/assault_agent/main.py &
sleep 2

echo "Starting Evaluator Agent..."
$PYTHON_EXEC /home/yl/yl/jzz/A2A/evaluator_agent/main.py &
sleep 2

echo "Starting Closed Loop Optimization Agent..."
$PYTHON_EXEC /home/yl/yl/jzz/A2A/closed_loop_agent/main.py &
sleep 2

echo "Starting Commander Agent..."
$PYTHON_EXEC /home/yl/yl/jzz/A2A/commander_agent/main.py

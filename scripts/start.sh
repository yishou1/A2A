#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

OFFLINE=0
REQUIRE_LLM=0
for arg in "$@"; do
  case "$arg" in
    --offline) OFFLINE=1 ;;
    --require-llm) REQUIRE_LLM=1 ;;
    *) echo "Usage: $0 [--offline] [--require-llm]" >&2; exit 2 ;;
  esac
done

load_root_env
resolve_a2a_python

if [[ "$OFFLINE" == 1 ]]; then
  export ENABLE_LLM=false
  export ALGOLIB_ENABLE_LLM=false
elif [[ "${ENABLE_LLM:-false}" == "true" && -z "${AZURE_OPENAI_API_KEY:-}" ]]; then
  if [[ "$REQUIRE_LLM" == 1 ]]; then
    echo "ENABLE_LLM=true but AZURE_OPENAI_API_KEY is empty." >&2
    exit 1
  fi
  echo "[notice] Azure key is empty; using deterministic algorithm planning for this run."
  export ENABLE_LLM=false
  export ALGOLIB_ENABLE_LLM=false
fi

export PYTHONPATH="$COMMANDER_DIR:$COMMANDER_DIR/services:$AMOS_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
export NACOS_ADDR="${NACOS_ADDR:-127.0.0.1:8848}"
export NACOS_SERVER="$NACOS_ADDR"
export NACOS_ENABLED=true
export A2A_SERVICE_IP="${A2A_SERVICE_IP:-127.0.0.1}"
export A2A_HEARTBEAT_INTERVAL="${A2A_HEARTBEAT_INTERVAL:-5}"
export HEARTBEAT_INTERVAL="$A2A_HEARTBEAT_INTERVAL"
if [[ "${ENABLE_LLM:-false}" == "true" ]]; then
  export A2A_REQUEST_TIMEOUT="${A2A_REQUEST_TIMEOUT:-180}"
  export LLM_TIMEOUT_SECONDS="${LLM_TIMEOUT_SECONDS:-60}"
else
  export A2A_REQUEST_TIMEOUT="${A2A_REQUEST_TIMEOUT:-60}"
fi
export ALGOLIB_BASE_URL="${ALGOLIB_BASE_URL:-http://127.0.0.1:8088}"
export ALGOLIB_REGISTRY_PATH="$ALGOLIB_DIR/registry.json"
export ALGOLIB_EXECUTION_LOG_PATH="$ALGOLIB_DIR/executions.jsonl"
export ALGORITHM_LIBRARY_ENABLED=true
export ALGORITHM_LIBRARY_REQUIRED=true
export TASK_SCHEDULING_USE_ALGOLIB=true
export TIA_ALGOLIB_CALL_MODE=run
export TIA_ALGORITHM_PLANNER="$([[ "${ENABLE_LLM:-false}" == "true" ]] && echo llm || echo fixed)"
export TASK_SCHEDULING_ALGORITHM_PLANNER="$TIA_ALGORITHM_PLANNER"
export A2A_BACKEND_MODE="${A2A_BACKEND_MODE:-gateway}"
export A2A_GATEWAY_URL="${A2A_GATEWAY_URL:-http://127.0.0.1:8030}"
export A2A_COMMANDER_URL="${A2A_COMMANDER_URL:-http://127.0.0.1:8021}"
export AMOS_BASE_URL="${AMOS_BASE_URL:-http://127.0.0.1:5000}"
export COMMANDER_BASE_URL="${COMMANDER_BASE_URL:-http://127.0.0.1:8021}"
export GATEWAY_PUBLIC_BASE_URL="${GATEWAY_PUBLIC_BASE_URL:-http://127.0.0.1:8030}"

echo "[infra] checking Docker Desktop connection"
docker info >/dev/null
docker compose -f "$COMMANDER_DIR/docker-compose.yml" up -d nacos auth-server
wait_http "Nacos" "http://127.0.0.1:8848/nacos/v1/console/health/readiness" 120
wait_http "A2A auth mock" "http://127.0.0.1:8080/get" 60

declare -a TIA_SERVICES=(
  battlefield_rtdetr_detector
  siamese_mask2former_damage
  edl_evidential_verifier
  motr_neural_kalman_tracker
  marl_ppo_task_scheduler
  imagebind_multimodal_encoder
  multimodal_mamba_fusion
  supcon_meta_classifier
  synapse_rag_retriever
  knowledge_semantic_comm
  marl_dynamic_router
)
declare -a TIA_PORTS=(9020 9021 9022 9023 9024 9025 9026 9027 9028 9029 9030)

echo "[algorithms] starting HTTP services"
for index in "${!TIA_SERVICES[@]}"; do
  name="${TIA_SERVICES[$index]}"
  port="${TIA_PORTS[$index]}"
  start_service "algorithm-$name" "$COMMANDER_DIR" "http://127.0.0.1:$port/health" \
    env PORT="$port" "$A2A_PYTHON" "services/$name/app/main.py"
done

start_service "algorithm-track-threat" "$COMMANDER_DIR" "http://127.0.0.1:9042/health" \
  env PORT=9042 "$A2A_PYTHON" services/track_threat_algorithms/app/main.py
start_service "algorithm-decision-planning" "$COMMANDER_DIR" "http://127.0.0.1:9040/health" \
  env PORT=9040 "$A2A_PYTHON" services/decision_planning_core/app/main.py
start_service "algorithm-compliance" "$COMMANDER_DIR" "http://127.0.0.1:9041/health" \
  env PORT=9041 "$A2A_PYTHON" services/compliance_authorization_core/app/main.py
start_service "algorithm-execution" "$COMMANDER_DIR" "http://127.0.0.1:9012/health" \
  env PORT=9012 "$A2A_PYTHON" services/execution_control_planner/app/main.py
for item in mission_feature_adapter:9013 mission_completion_scorer:9014 closed_loop_decision_advisor:9015; do
  name="${item%%:*}"
  port="${item##*:}"
  start_service "algorithm-$name" "$COMMANDER_DIR" "http://127.0.0.1:$port/health" \
    env PORT="$port" "$A2A_PYTHON" "services/$name/app/main.py"
done
start_service "algorithm-xbd_damage_assessor" "$COMMANDER_DIR" "http://127.0.0.1:9016/health" \
  env PORT=9016 "$A2A_PYTHON" services/xbd_damage_assessor/app/main.py

declare -a ALGORITHM_CARDS=(
  battlefield_rtdetr_detector siamese_mask2former_damage edl_evidential_verifier
  motr_neural_kalman_tracker marl_ppo_task_scheduler imagebind_multimodal_encoder
  multimodal_mamba_fusion supcon_meta_classifier synapse_rag_retriever
  knowledge_semantic_comm marl_dynamic_router multimodal_feature_fuser
  target_type_classifier track_state_updater trajectory_predictor graph_relation_reasoner
  decision_planning_core compliance_authorization_core execution_control_planner
  mission_feature_adapter mission_completion_scorer closed_loop_decision_advisor
  xbd_damage_assessor
)

echo "[algorithms] registering and activating packages"
for algorithm_id in "${ALGORITHM_CARDS[@]}"; do
  if ! "$COMMANDER_DIR/build/algolib" show-card "$algorithm_id" 1.0.0 python_http_service >/dev/null 2>&1; then
    "$COMMANDER_DIR/build/algolib" register \
      "$COMMANDER_DIR/examples/$algorithm_id/1.0.0/algorithm_card.yaml" >/dev/null
  fi
  "$COMMANDER_DIR/build/algolib" validate "$algorithm_id" 1.0.0 python_http_service >/dev/null
  "$COMMANDER_DIR/build/algolib" activate "$algorithm_id" 1.0.0 python_http_service >/dev/null
done

start_service algolib "$COMMANDER_DIR" "http://127.0.0.1:8088/health" \
  "$COMMANDER_DIR/build/algolib_server" --host 127.0.0.1 --port 8088 \
  --registry "$ALGOLIB_REGISTRY_PATH" --execution-log "$ALGOLIB_EXECUTION_LOG_PATH"

echo "[agents] starting independent A2A processes"
start_service agent-tactical-intelligence "$COMMANDER_DIR" "http://127.0.0.1:10200/health" \
  env TIA_PORT=10200 TIA_NACOS_REGISTER=1 "$A2A_PYTHON" -m tactical_intelligence_agent.main
start_service agent-track-threat "$COMMANDER_DIR" "http://127.0.0.1:8102/health" \
  env SERVICE_PORT=8102 SERVICE_IP=127.0.0.1 SERVICE_NAME=A2A-Agent AGENT_ROLE=track_threat \
  "$A2A_PYTHON" -m uvicorn track_threat_agent.app.main:app --host 127.0.0.1 --port 8102
start_service agent-task-scheduling "$COMMANDER_DIR" "http://127.0.0.1:10201/health" \
  env TASK_SCHEDULING_AGENT_PORT=10201 "$A2A_PYTHON" -m task_scheduling_agent.main
start_service agent-decision-planning "$COMMANDER_DIR" "http://127.0.0.1:10202/health" \
  env DECISION_PLANNING_AGENT_PORT=10202 "$A2A_PYTHON" -m decision_planning_agent.main
start_service agent-compliance "$COMMANDER_DIR" "http://127.0.0.1:10203/health" \
  env COMPLIANCE_AUTHORIZATION_AGENT_PORT=10203 "$A2A_PYTHON" -m compliance_authorization_agent.main
start_service agent-simulation-execution "$COMMANDER_DIR" "http://127.0.0.1:10204/health" \
  env EXECUTION_CONTROL_AGENT_ROLE=simulation_execution SIMULATION_EXECUTION_AGENT_PORT=10204 \
  "$A2A_PYTHON" -m execution_control_agent.main
start_service agent-closed-loop "$COMMANDER_DIR" "http://127.0.0.1:10205/health" \
  env CLOSED_LOOP_AGENT_PORT=10205 "$A2A_PYTHON" -m closed_loop_agent.main

start_service commander-manager "$COMMANDER_DIR" "http://127.0.0.1:8021/health" \
  "$A2A_PYTHON" commander_agent/main.py --mode remote --workflow bpel \
  --serve-workflow-manager --manager-host 127.0.0.1 --manager-port 8021 \
  --state-dir "$RUNTIME_DIR/workflows"
start_service amos-platform "$AMOS_DIR" "http://127.0.0.1:5000/api/v1/scenarios" \
  "$A2A_PYTHON" -m amos_platform.api.app_factory --host 127.0.0.1 --port 5000
start_service commander-gateway "$COMMANDER_DIR" "http://127.0.0.1:8030/gateway/v1/health" \
  "$A2A_PYTHON" -m commander_gateway --host 127.0.0.1 --port 8030

echo
echo "System is running:"
echo "  AMOS UI:       http://127.0.0.1:5000/"
echo "  Commander:     http://127.0.0.1:8021/supervisor"
echo "  Nacos console: http://127.0.0.1:8848/nacos/"
echo "  Planner mode:  $TIA_ALGORITHM_PLANNER"
echo "  Logs:          $LOG_DIR"

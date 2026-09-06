#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

OFFLINE=0
REQUIRE_LLM=0
LLM_PROFILE_OVERRIDE=""

usage() {
  cat >&2 <<'EOF'
Usage: scripts/start.sh [--offline] [--require-llm] [--llm-profile PROFILE]

LLM profiles:
  azure            Use Azure OpenAI / API-hosted model from .env AZURE_OPENAI_*.
  local-qwen-gpu   Use/start local OpenAI-compatible Qwen service.
  offline          Disable LLM planning and use deterministic/fixed routing.

The LLM_PROFILE environment variable is also supported. Command-line
--llm-profile takes precedence over .env and environment values.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --offline) OFFLINE=1 ;;
    --require-llm) REQUIRE_LLM=1 ;;
    --llm-profile)
      if [[ $# -lt 2 || "$2" == --* ]]; then
        echo "--llm-profile requires a value." >&2
        usage
        exit 2
      fi
      LLM_PROFILE_OVERRIDE="$2"
      shift
      ;;
    --llm-profile=*)
      LLM_PROFILE_OVERRIDE="${1#*=}"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 2
      ;;
  esac
  shift
done

load_root_env
resolve_a2a_python

if [[ -n "$LLM_PROFILE_OVERRIDE" ]]; then
  export LLM_PROFILE="$LLM_PROFILE_OVERRIDE"
fi

case "${LLM_PROFILE:-}" in
  "")
    ;;
  azure|azure-openai|azure_openai)
    export ENABLE_LLM=true
    export LLM_PROVIDER=azure_openai
    export TOOL_LLM_URL="${AZURE_OPENAI_ENDPOINT:-${TOOL_LLM_URL:-}}"
    export TOOL_LLM_NAME="${AZURE_OPENAI_CHAT_DEPLOYMENT:-${AZURE_OPENAI_DEPLOYMENT:-${TOOL_LLM_NAME:-gpt-4o-mini}}}"
    export LLM_JSON_MODE="${LLM_JSON_MODE:-true}"
    ;;
  local|local-qwen|local-qwen-gpu|qwen-gpu)
    export ENABLE_LLM=true
    export LLM_PROVIDER=openai_compatible
    export TOOL_LLM_URL="${LOCAL_QWEN_BASE_URL:-http://127.0.0.1:${LOCAL_QWEN_PORT:-11435}/v1}"
    export TOOL_LLM_NAME="${LOCAL_QWEN_MODEL_NAME:-qwen3:1.7b}"
    export API_KEY="${API_KEY:-ollama}"
    export LOCAL_QWEN_DEVICE="${LOCAL_QWEN_DEVICE:-cuda}"
    export LOCAL_QWEN_DTYPE="${LOCAL_QWEN_DTYPE:-float16}"
    export LLM_TIMEOUT_SECONDS="${LLM_TIMEOUT_SECONDS:-120}"
    export LLM_MAX_TOKENS="${LLM_MAX_TOKENS:-512}"
    export LLM_TEMPERATURE="${LLM_TEMPERATURE:-0.1}"
    export LLM_JSON_MODE="${LLM_JSON_MODE:-true}"
    export LLM_STRIP_THINKING="${LLM_STRIP_THINKING:-true}"
    export LLM_JSON_RETRY_COUNT="${LLM_JSON_RETRY_COUNT:-1}"
    export LLM_REASONING_EFFORT="${LLM_REASONING_EFFORT:-none}"
    ;;
  offline|fixed|deterministic)
    export ENABLE_LLM=false
    export ALGOLIB_ENABLE_LLM=false
    export A2A_ACT_AGENT_LLM=false
    ;;
  *)
    echo "Unsupported LLM_PROFILE='$LLM_PROFILE'. Use azure, local-qwen-gpu, or offline." >&2
    exit 2
    ;;
esac

llm_provider="${LLM_PROVIDER:-azure_openai}"
llm_provider="${llm_provider,,}"
llm_api_key="${AZURE_OPENAI_API_KEY:-}"
if [[ "$llm_provider" != "azure" && "$llm_provider" != "azure_openai" ]]; then
  llm_api_key="${API_KEY:-${ALGOLIB_LLM_API_KEY:-}}"
fi

if [[ "$OFFLINE" == 1 ]]; then
  active_llm_profile="offline"
  export ENABLE_LLM=false
  export ALGOLIB_ENABLE_LLM=false
elif [[ "${ENABLE_LLM:-false}" == "true" && -z "$llm_api_key" ]]; then
  active_llm_profile="${LLM_PROFILE:-env-default}:disabled-no-key"
  if [[ "$REQUIRE_LLM" == 1 ]]; then
    echo "ENABLE_LLM=true but no LLM API key is configured for provider '$llm_provider'." >&2
    exit 1
  fi
  echo "[notice] LLM API key is empty; using deterministic algorithm planning for this run."
  export ENABLE_LLM=false
  export ALGOLIB_ENABLE_LLM=false
else
  active_llm_profile="${LLM_PROFILE:-env-default}"
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
  export A2A_HEARTBEAT_GRACE_SECONDS="${A2A_HEARTBEAT_GRACE_SECONDS:-$A2A_REQUEST_TIMEOUT}"
  export A2A_LEASE_HEARTBEAT_GRACE_SECONDS="${A2A_LEASE_HEARTBEAT_GRACE_SECONDS:-$A2A_REQUEST_TIMEOUT}"
  export A2A_ACCEPT_STALE_SUCCESS_RESPONSE="${A2A_ACCEPT_STALE_SUCCESS_RESPONSE:-true}"
else
  export A2A_REQUEST_TIMEOUT="${A2A_REQUEST_TIMEOUT:-60}"
fi
export ALGOLIB_BASE_URL="${ALGOLIB_BASE_URL:-http://127.0.0.1:8088}"
export ALGOLIB_TRANSPORT="${ALGOLIB_TRANSPORT:-gateway}"
export A2A_ALGORITHM_BACKEND="${A2A_ALGORITHM_BACKEND:-algolib}"
export TASK_SCHEDULING_BACKEND="${TASK_SCHEDULING_BACKEND:-algolib}"
export DECISION_AGENT_BACKEND="${DECISION_AGENT_BACKEND:-algolib}"
export EXECUTION_CONTROL_BACKEND="${EXECUTION_CONTROL_BACKEND:-algolib}"
export CLOSED_LOOP_BACKEND="${CLOSED_LOOP_BACKEND:-algolib}"
export ALGOLIB_FALLBACK_LOCAL="${ALGOLIB_FALLBACK_LOCAL:-true}"
export ALGOLIB_REGISTRY_PATH="$ALGOLIB_DIR/registry.json"
export ALGOLIB_EXECUTION_LOG_PATH="$ALGOLIB_DIR/executions.jsonl"
export ALGOLIB_FUNCTION_CATALOG_PATH="${ALGOLIB_FUNCTION_CATALOG_PATH:-$COMMANDER_DIR/config/operational_function_catalog.yaml}"
export ALGORITHM_LIBRARY_ENABLED=true
export ALGORITHM_LIBRARY_REQUIRED=true
export A2A_FORCE_ALGOLIB_FIRST="${A2A_FORCE_ALGOLIB_FIRST:-1}"
export TASK_SCHEDULING_USE_ALGOLIB=true
export TIA_ALGOLIB_CALL_MODE=run
export TIA_ALGORITHM_PLANNER="$([[ "${ENABLE_LLM:-false}" == "true" ]] && echo llm || echo fixed)"
export TASK_SCHEDULING_ALGORITHM_PLANNER="$TIA_ALGORITHM_PLANNER"
export A2A_ACT_AGENT_LLM="${A2A_ACT_AGENT_LLM:-${ALGOLIB_ENABLE_LLM:-${ENABLE_LLM:-false}}}"
export A2A_BACKEND_MODE="${A2A_BACKEND_MODE:-gateway}"
export A2A_GATEWAY_URL="${A2A_GATEWAY_URL:-http://127.0.0.1:8030}"
export A2A_COMMANDER_URL="${A2A_COMMANDER_URL:-http://127.0.0.1:8021}"
export AMOS_BASE_URL="${AMOS_BASE_URL:-http://127.0.0.1:5000}"
export COMMANDER_BASE_URL="${COMMANDER_BASE_URL:-http://127.0.0.1:8021}"
export GATEWAY_PUBLIC_BASE_URL="${GATEWAY_PUBLIC_BASE_URL:-http://127.0.0.1:8030}"
PUBLIC_BIND_HOST="${PUBLIC_BIND_HOST:-127.0.0.1}"

algorithm_card_rows() {
  "$A2A_PYTHON" - "$COMMANDER_DIR/examples" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

root = Path(sys.argv[1])


def field(text: str, name: str, default: str = "") -> str:
    match = re.search(rf"^{re.escape(name)}:\s*([^\s#]+)", text, re.MULTILINE)
    return match.group(1).strip() if match else default


def runtime_field(text: str, name: str, default: str = "") -> str:
    match = re.search(rf"^\s*{re.escape(name)}:\s*([^\s#]+)", text, re.MULTILINE)
    return match.group(1).strip() if match else default


for card in sorted(root.glob("*/1.0.0/algorithm_card.yaml")):
    text = card.read_text(encoding="utf-8")
    package_dir = card.parents[1].name
    algorithm_id = field(text, "algorithm_id", package_dir)
    version = field(text, "version", "1.0.0")
    backend_type = field(text, "backend_type", runtime_field(text, "backend_type", ""))
    health_endpoint = runtime_field(text, "health_endpoint", "")
    if not backend_type:
        continue
    print("\t".join([package_dir, algorithm_id, version, backend_type, health_endpoint]))
PY
}

url_port() {
  "$A2A_PYTHON" - "$1" <<'PY'
from urllib.parse import urlparse
import sys

parsed = urlparse(sys.argv[1])
if parsed.port:
    print(parsed.port)
elif parsed.scheme == "https":
    print(443)
else:
    print(80)
PY
}

url_host() {
  "$A2A_PYTHON" - "$1" <<'PY'
from urllib.parse import urlparse
import sys

print(urlparse(sys.argv[1]).hostname or "127.0.0.1")
PY
}

is_track_threat_mounted_algorithm() {
  case "$1" in
    multimodal_feature_fuser|target_type_classifier|track_state_updater|trajectory_predictor|graph_relation_reasoner)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

echo "[infra] checking Docker Desktop connection"
docker info >/dev/null
docker compose -f "$COMMANDER_DIR/docker-compose.yml" up -d nacos auth-server
wait_http "Nacos" "http://127.0.0.1:8848/nacos/v1/console/health/readiness" 120
wait_http "A2A auth mock" "http://127.0.0.1:8080/get" 60

llm_url="${TOOL_LLM_URL:-}"
if [[ "${ENABLE_LLM:-false}" == "true" && "$llm_provider" != "azure" && "$llm_provider" != "azure_openai" ]]; then
  llm_port=""
  if [[ "$llm_url" == http://127.0.0.1:* || "$llm_url" == http://localhost:* ]]; then
    llm_host_port="${llm_url#http://}"
    llm_host_port="${llm_host_port%%/*}"
    llm_port="${llm_host_port##*:}"
  fi
  if [[ "$llm_port" =~ ^[0-9]+$ ]]; then
    if curl --noproxy '*' -fsS --max-time 2 "http://127.0.0.1:$llm_port/health" >/dev/null 2>&1 \
      || curl --noproxy '*' -fsS --max-time 2 "http://127.0.0.1:$llm_port/api/tags" >/dev/null 2>&1; then
      echo "[running] local LLM endpoint at 127.0.0.1:$llm_port"
    else
      echo "[llm] starting local Qwen endpoint"
      export LOCAL_QWEN_DEVICE="${LOCAL_QWEN_DEVICE:-cuda}"
      export LOCAL_QWEN_DTYPE="${LOCAL_QWEN_DTYPE:-float16}"
      qwen_model_dir="${LOCAL_QWEN_MODEL_DIR:-$ROOT_DIR/local_models/qwen3-1.7b}"
      if [[ ! -f "$qwen_model_dir/config.json" \
        && -f "$ROOT_DIR/../local_models/qwen3-1.7b/config.json" ]]; then
        qwen_model_dir="$ROOT_DIR/../local_models/qwen3-1.7b"
      fi
      start_service "local-qwen" "$ROOT_DIR" "http://127.0.0.1:$llm_port/health" \
        "$A2A_PYTHON" scripts/local_qwen_openai_server.py \
        --host 127.0.0.1 --port "$llm_port" \
        --model-dir "$qwen_model_dir" \
        --model-name "${TOOL_LLM_NAME:-qwen3:1.7b}"
    fi
  fi
fi

if [[ "${ALGOLIB_RESET_REGISTRY:-true}" == "true" ]]; then
  "$A2A_PYTHON" - "$ALGOLIB_REGISTRY_PATH" "$ALGOLIB_EXECUTION_LOG_PATH" <<'PY'
from pathlib import Path
import sys

for item in sys.argv[1:]:
    path = Path(item)
    if path.exists() and path.is_file():
        path.unlink()
PY
fi

mapfile -t ALGORITHM_CARD_ROWS < <(algorithm_card_rows)
ALGOLIB_REAL_ONNX=0
if [[ -f "$COMMANDER_DIR/build/CMakeCache.txt" ]] \
  && grep -q '^ALGOLIB_WITH_ONNXRUNTIME:BOOL=ON$' "$COMMANDER_DIR/build/CMakeCache.txt"; then
  ALGOLIB_REAL_ONNX=1
fi

echo "[algorithms] starting HTTP services"
track_threat_algorithms_started=0
for row in "${ALGORITHM_CARD_ROWS[@]}"; do
  IFS=$'\t' read -r package_dir algorithm_id version backend_type health_endpoint <<< "$row"
  if [[ "$backend_type" != "python_http_service" ]]; then
    continue
  fi
  if is_track_threat_mounted_algorithm "$algorithm_id"; then
    if [[ "$track_threat_algorithms_started" == 0 ]]; then
      port="$(url_port "$health_endpoint")"
      start_service "algorithm-track-threat" "$COMMANDER_DIR" "http://127.0.0.1:$port/health" \
        env PORT="$port" "$A2A_PYTHON" services/track_threat_algorithms/app/main.py
      track_threat_algorithms_started=1
    fi
    continue
  fi
  if [[ ! -f "$COMMANDER_DIR/services/$package_dir/app/main.py" ]]; then
    echo "[skip] $algorithm_id has no bundled runtime service; not registering it for this demo."
    continue
  fi
  port="$(url_port "$health_endpoint")"
  host="$(url_host "$health_endpoint")"
  if [[ "$host" != "127.0.0.1" && "$host" != "localhost" ]]; then
    echo "[skip] $algorithm_id uses non-local health endpoint $health_endpoint."
    continue
  fi
  start_service "algorithm-$algorithm_id" "$COMMANDER_DIR" "$health_endpoint" \
    env PORT="$port" "$A2A_PYTHON" "services/$package_dir/app/main.py"
done

echo "[algorithms] registering and activating packages"
for row in "${ALGORITHM_CARD_ROWS[@]}"; do
  IFS=$'\t' read -r package_dir algorithm_id version backend_type health_endpoint <<< "$row"
  if [[ "$backend_type" == "onnx" && "$ALGOLIB_REAL_ONNX" != 1 \
    && "${ALGOLIB_REGISTER_STUB_ONNX:-false}" != "true" \
    && "$algorithm_id" != "onnx_text_classifier" ]]; then
    echo "[skip] $algorithm_id requires real ONNX Runtime; current algolib build uses stub."
    continue
  fi
  if [[ "$backend_type" == "python_http_service" \
    && ! -f "$COMMANDER_DIR/services/$package_dir/app/main.py" ]]; then
    if ! is_track_threat_mounted_algorithm "$algorithm_id"; then
      continue
    fi
  fi
  "$COMMANDER_DIR/build/algolib" register \
    "$COMMANDER_DIR/examples/$package_dir/$version/algorithm_card.yaml" >/dev/null
  "$COMMANDER_DIR/build/algolib" validate "$algorithm_id" "$version" "$backend_type" >/dev/null
  "$COMMANDER_DIR/build/algolib" activate "$algorithm_id" "$version" "$backend_type" >/dev/null
done

start_service algolib "$COMMANDER_DIR" "http://127.0.0.1:8088/health" \
  "$COMMANDER_DIR/build/algolib_server" --host 127.0.0.1 --port 8088 \
  --registry "$ALGOLIB_REGISTRY_PATH" --execution-log "$ALGOLIB_EXECUTION_LOG_PATH"

echo "[agents] starting independent A2A processes"
start_service agent-tactical-intelligence "$COMMANDER_DIR" "http://127.0.0.1:10200/health" \
  env TIA_PORT=10200 TIA_NACOS_REGISTER=1 "$A2A_PYTHON" -m tactical_intelligence_agent.main
start_service agent-track-threat "$COMMANDER_DIR" "http://127.0.0.1:8102/health" \
  env NACOS_ENABLED=true SERVICE_PORT=8102 SERVICE_IP=127.0.0.1 SERVICE_NAME=A2A-Agent AGENT_ROLE=track_threat \
  ALGORITHM_LIBRARY_ENABLED=true ALGORITHM_LIBRARY_REQUIRED=true ALGOLIB_BASE_URL="${ALGOLIB_BASE_URL:-http://127.0.0.1:8088}" \
  "$A2A_PYTHON" -m uvicorn track_threat_agent.app.main:app --host 127.0.0.1 --port 8102
start_service agent-task-scheduling "$COMMANDER_DIR" "http://127.0.0.1:10201/health" \
  env TASK_SCHEDULING_AGENT_PORT=10201 "$A2A_PYTHON" -m task_scheduling_agent.main
start_service agent-decision-planning "$COMMANDER_DIR" "http://127.0.0.1:10202/health" \
  env DECISION_PLANNING_AGENT_PORT=10202 DECISION_AGENT_BACKEND="$DECISION_AGENT_BACKEND" \
  DECISION_AGENT_ALGOLIB_LLM="${DECISION_AGENT_ALGOLIB_LLM:-false}" \
  "$A2A_PYTHON" -m decision_planning_agent.main
start_service agent-compliance "$COMMANDER_DIR" "http://127.0.0.1:10203/health" \
  env COMPLIANCE_AUTHORIZATION_AGENT_PORT=10203 DECISION_AGENT_BACKEND="$DECISION_AGENT_BACKEND" \
  DECISION_AGENT_ALGOLIB_LLM="${DECISION_AGENT_ALGOLIB_LLM:-false}" \
  "$A2A_PYTHON" -m compliance_authorization_agent.main
start_service agent-simulation-execution "$COMMANDER_DIR" "http://127.0.0.1:10204/health" \
  env ALGOLIB_ENABLE_LLM="$A2A_ACT_AGENT_LLM" EXECUTION_CONTROL_BACKEND="$EXECUTION_CONTROL_BACKEND" \
  EXECUTION_CONTROL_AGENT_ROLE=simulation_execution SIMULATION_EXECUTION_AGENT_PORT=10204 \
  "$A2A_PYTHON" -m execution_control_agent.main
start_service agent-closed-loop "$COMMANDER_DIR" "http://127.0.0.1:10205/health" \
  env ALGOLIB_ENABLE_LLM="$A2A_ACT_AGENT_LLM" CLOSED_LOOP_BACKEND="$CLOSED_LOOP_BACKEND" \
  CLOSED_LOOP_AGENT_PORT=10205 "$A2A_PYTHON" -m closed_loop_agent.main

start_service commander-manager "$COMMANDER_DIR" "http://127.0.0.1:8021/health" \
  "$A2A_PYTHON" commander_agent/main.py --mode remote --workflow bpel \
  --serve-workflow-manager --manager-host 127.0.0.1 --manager-port 8021 \
  --state-dir "$RUNTIME_DIR/workflows"
start_service amos-platform "$AMOS_DIR" "http://127.0.0.1:5000/api/v1/scenarios" \
  "$A2A_PYTHON" -m amos_platform.api.app_factory --host "$PUBLIC_BIND_HOST" --port 5000
start_service commander-gateway "$COMMANDER_DIR" "http://127.0.0.1:8030/gateway/v1/health" \
  "$A2A_PYTHON" -m commander_gateway --host "$PUBLIC_BIND_HOST" --port 8030

echo
echo "System is running:"
echo "  AMOS UI:       http://127.0.0.1:5000/"
echo "  Commander:     http://127.0.0.1:8021/supervisor"
echo "  Nacos console: http://127.0.0.1:8848/nacos/"
echo "  LLM profile:   $active_llm_profile"
echo "  LLM provider:  ${LLM_PROVIDER:-disabled}"
echo "  Planner mode:  $TIA_ALGORITHM_PLANNER"
echo "  Logs:          $LOG_DIR"
echo
echo "Note: if you change --llm-profile, run ./scripts/stop.sh before ./scripts/start.sh so"
echo "      already-running Agent processes reload the new environment."

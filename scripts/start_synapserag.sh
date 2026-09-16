#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_DIR="$ROOT_DIR/algorithmrepo/services/synapserag"
ENV_FILE="${SYNAPSERAG_ENV_FILE:-$ROOT_DIR/.runtime/synapserag/service.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

: "${SYNAPSERAG_PYTHON:=python3}"
: "${SYNAPSERAG_SAVE_DIR:=$ROOT_DIR/algorithmrepo/knowledge_bases/newport_roe_handbook_2022}"
: "${SYNAPSERAG_RECORDS_DIR:=$SYNAPSERAG_SAVE_DIR/records}"
: "${SYNAPSERAG_INDEX_ID:=newport-roe-handbook-2022-v1}"
PYTHON_BIN="$(command -v "$SYNAPSERAG_PYTHON" 2>/dev/null || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "Python executable not found: $SYNAPSERAG_PYTHON" >&2
  exit 1
fi
DATA_DIR="$(realpath -m "$SYNAPSERAG_SAVE_DIR")"
RECORDS_DIR="$(realpath -m "$SYNAPSERAG_RECORDS_DIR")"
KNOWLEDGE_ROOT="$(realpath -m "$ROOT_DIR/algorithmrepo/knowledge_bases")"
case "$DATA_DIR/" in
  "$KNOWLEDGE_ROOT/"*) ;;
  *) echo "SYNAPSERAG_SAVE_DIR must be inside $KNOWLEDGE_ROOT" >&2; exit 1 ;;
esac
case "$RECORDS_DIR/" in
  "$DATA_DIR/"*) ;;
  *) echo "SYNAPSERAG_RECORDS_DIR must be inside $DATA_DIR" >&2; exit 1 ;;
esac

export SYNAPSERAG_SAVE_DIR="$DATA_DIR"
export SYNAPSERAG_RECORDS_DIR="$RECORDS_DIR"
export SYNAPSERAG_INDEX_ID
export PYTHONPATH="$SERVICE_DIR:$SERVICE_DIR/src"
export PYTHONDONTWRITEBYTECODE=1
cd "$SERVICE_DIR"
exec "$PYTHON_BIN" -m uvicorn api_server:app \
  --host "${SYNAPSERAG_HOST:-127.0.0.1}" \
  --port "${SYNAPSERAG_PORT:-8000}"

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

: "${SYNAPSERAG_PYTHON:?Set SYNAPSERAG_PYTHON to an external environment Python}"
: "${SYNAPSERAG_SAVE_DIR:?Set SYNAPSERAG_SAVE_DIR to an external data directory}"
if [[ ! -x "$SYNAPSERAG_PYTHON" ]]; then
  echo "Python executable not found: $SYNAPSERAG_PYTHON" >&2
  exit 1
fi
if [[ "$SYNAPSERAG_SAVE_DIR" != /* ]]; then
  echo 'SYNAPSERAG_SAVE_DIR must be an absolute path.' >&2
  exit 1
fi
DATA_DIR="$(realpath -m "$SYNAPSERAG_SAVE_DIR")"
case "$DATA_DIR/" in
  "$ROOT_DIR/"*) echo 'Data must live outside the A2A repository.' >&2; exit 1 ;;
esac

export SYNAPSERAG_SAVE_DIR="$DATA_DIR"
export PYTHONPATH="$SERVICE_DIR:$SERVICE_DIR/src"
export PYTHONDONTWRITEBYTECODE=1
cd "$SERVICE_DIR"
exec "$SYNAPSERAG_PYTHON" -m uvicorn api_server:app \
  --host "${SYNAPSERAG_HOST:-127.0.0.1}" \
  --port "${SYNAPSERAG_PORT:-8000}"

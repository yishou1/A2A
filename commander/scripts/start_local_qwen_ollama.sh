#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="${A2A_WORKSPACE_ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
OLLAMA_BIN="${OLLAMA_BIN:-${WORKSPACE_ROOT}/tools/ollama-runtime/bin/ollama}"
OLLAMA_LIB_DIR="${OLLAMA_LIB_DIR:-${WORKSPACE_ROOT}/tools/ollama-runtime/lib/ollama}"
OLLAMA_MODELS="${OLLAMA_MODELS:-${WORKSPACE_ROOT}/local_models/ollama}"

if [[ ! -x "${OLLAMA_BIN}" ]]; then
    echo "Ollama binary not found: ${OLLAMA_BIN}" >&2
    exit 1
fi

mkdir -p "${OLLAMA_MODELS}"

export OLLAMA_MODELS
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-4096}"
export OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-1}"
export LD_LIBRARY_PATH="${OLLAMA_LIB_DIR}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

exec "${OLLAMA_BIN}" serve

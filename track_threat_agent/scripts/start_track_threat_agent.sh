#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_DIR="$(cd "${AGENT_DIR}/.." && pwd)"

export PYTHONPATH="${REPO_DIR}:${PYTHONPATH:-}"
export SERVICE_NAME="${SERVICE_NAME:-A2A-Agent}"
export AGENT_ROLE="${AGENT_ROLE:-track_threat}"
export AGENT_STATUS="${AGENT_STATUS:-idle}"
export SERVICE_IP="${SERVICE_IP:-127.0.0.1}"
export SERVICE_PORT="${SERVICE_PORT:-8102}"

cd "${AGENT_DIR}"
exec uv run --with-requirements requirements.txt --with-requirements ../requirements.txt \
  uvicorn app.main:app --host 0.0.0.0 --port "${SERVICE_PORT}"

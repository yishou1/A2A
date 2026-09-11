#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

KEEP_NACOS=0
[[ "${1:-}" == "--keep-nacos" ]] && KEEP_NACOS=1
if [[ $# -gt 1 || ( $# -eq 1 && "${1:-}" != "--keep-nacos" ) ]]; then
  echo "Usage: $0 [--keep-nacos]" >&2
  exit 2
fi

mapfile -t names < <(find "$PID_DIR" -maxdepth 1 -type f -name '*.pid' -printf '%f\n' 2>/dev/null | sed 's/\.pid$//' | sort -r)
for name in "${names[@]}"; do
  stop_service "$name"
done

if [[ "$KEEP_NACOS" == 0 ]]; then
  docker compose -f "$COMMANDER_DIR/docker-compose.yml" stop nacos >/dev/null
  echo "[stopped] nacos"
fi
docker compose -f "$COMMANDER_DIR/docker-compose.yml" stop auth-server >/dev/null
echo "[stopped] auth-server"

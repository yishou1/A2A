#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

printf '%-38s %-10s %s\n' SERVICE STATUS PID
printf '%-38s %-10s %s\n' '--------------------------------------' '----------' '--------'
if [[ -d "$PID_DIR" ]]; then
  while IFS= read -r file; do
    name="$(basename "$file" .pid)"
    pid="$(tr -d '[:space:]' < "$file")"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      printf '%-38s %-10s %s\n' "$name" running "$pid"
    else
      printf '%-38s %-10s %s\n' "$name" stopped "${pid:--}"
    fi
  done < <(find "$PID_DIR" -maxdepth 1 -type f -name '*.pid' -print | sort)
fi

if docker info >/dev/null 2>&1; then
  nacos_status="$(docker inspect -f '{{.State.Status}}' a2a-nacos-standalone 2>/dev/null || true)"
  printf '%-38s %-10s %s\n' nacos "${nacos_status:-absent}" container
else
  printf '%-38s %-10s %s\n' docker unavailable '-'
fi

echo
for endpoint in \
  'AMOS|http://127.0.0.1:5000/api/v1/scenarios' \
  'Commander|http://127.0.0.1:8021/health' \
  'Gateway|http://127.0.0.1:8030/gateway/v1/health' \
  'AlgoLib|http://127.0.0.1:8088/health' \
  'A2A auth|http://127.0.0.1:8080/get' \
  'Nacos|http://127.0.0.1:8848/nacos/v1/console/health/readiness'; do
  label="${endpoint%%|*}"
  url="${endpoint#*|}"
  if curl --noproxy '*' -fsS --max-time 2 "$url" >/dev/null 2>&1; then
    echo "[ok]      $label $url"
  else
    echo "[offline] $label $url"
  fi
done

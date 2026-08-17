#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMMANDER_DIR="$ROOT_DIR/commander"
AMOS_DIR="$ROOT_DIR/amos-platform"
RUNTIME_DIR="$ROOT_DIR/.runtime"
LOG_DIR="$RUNTIME_DIR/logs"
PID_DIR="$RUNTIME_DIR/pids"
ALGOLIB_DIR="$RUNTIME_DIR/algolib"

mkdir -p "$LOG_DIR" "$PID_DIR" "$ALGOLIB_DIR"

load_root_env() {
  if [[ -f "$ROOT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$ROOT_DIR/.env"
    set +a
  fi
  export NO_PROXY="${NO_PROXY:+$NO_PROXY,}127.0.0.1,localhost,0.0.0.0"
  export no_proxy="$NO_PROXY"
}

resolve_a2a_python() {
  if [[ -n "${A2A_CONDA_PREFIX:-}" && -x "$A2A_CONDA_PREFIX/bin/python" ]]; then
    A2A_PYTHON="$A2A_CONDA_PREFIX/bin/python"
  else
    local prefix
    prefix="$(conda env list | awk '$1 == "a2a" {print $NF; exit}')"
    if [[ -z "$prefix" || ! -x "$prefix/bin/python" ]]; then
      echo "Conda environment 'a2a' was not found." >&2
      return 1
    fi
    A2A_PYTHON="$prefix/bin/python"
  fi
  export A2A_PYTHON
}

pid_file() {
  printf '%s/%s.pid\n' "$PID_DIR" "$1"
}

service_pid() {
  local file
  file="$(pid_file "$1")"
  [[ -f "$file" ]] && tr -d '[:space:]' < "$file"
}

service_running() {
  local pid
  pid="$(service_pid "$1")"
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null
}

wait_http() {
  local name="$1"
  local url="$2"
  local timeout="${3:-60}"
  local deadline=$((SECONDS + timeout))
  while (( SECONDS < deadline )); do
    if curl --noproxy '*' -fsS --max-time 2 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  echo "$name did not become ready at $url within ${timeout}s." >&2
  return 1
}

start_service() {
  local name="$1"
  local workdir="$2"
  local health_url="$3"
  shift 3

  if service_running "$name"; then
    echo "[running] $name (pid $(service_pid "$name"))"
    return 0
  fi
  rm -f "$(pid_file "$name")"
  (
    cd "$workdir"
    exec setsid "$@" >"$LOG_DIR/$name.log" 2>&1 < /dev/null
  ) &
  local pid=$!
  printf '%s\n' "$pid" > "$(pid_file "$name")"
  if ! wait_http "$name" "$health_url" "${STARTUP_TIMEOUT:-90}"; then
    echo "Last log lines from $LOG_DIR/$name.log:" >&2
    tail -n 30 "$LOG_DIR/$name.log" >&2 || true
    return 1
  fi
  echo "[started] $name (pid $pid)"
}

stop_service() {
  local name="$1"
  local pid
  pid="$(service_pid "$name")"
  if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
    rm -f "$(pid_file "$name")"
    return 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$(pid_file "$name")"
    return 0
  fi
  kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
  local deadline=$((SECONDS + 10))
  while kill -0 "$pid" 2>/dev/null && (( SECONDS < deadline )); do
    sleep 0.2
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$(pid_file "$name")"
  echo "[stopped] $name"
}


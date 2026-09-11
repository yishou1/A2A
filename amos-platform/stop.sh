#!/usr/bin/env bash
# ═══ AMOS 场景仿真平台 — 一键停止 ═══
set -euo pipefail
cd "$(dirname "$0")"

PIDFILE=".amos.pid"
if [ ! -f "$PIDFILE" ]; then
  echo "服务未在运行（无 PID 文件）"
  exit 0
fi

PID="$(cat "$PIDFILE")"
if ! kill -0 "$PID" 2>/dev/null; then
  echo "服务未在运行（pid $PID 已退出），清理 PID 文件"
  rm -f "$PIDFILE"
  exit 0
fi

echo "▶ 停止服务 (pid $PID) ..."
kill "$PID"
for _ in $(seq 1 20); do
  kill -0 "$PID" 2>/dev/null || break
  sleep 0.3
done
if kill -0 "$PID" 2>/dev/null; then
  echo "✘ 未能正常停止，强制执行 ..."
  kill -9 "$PID" 2>/dev/null || true
fi
rm -f "$PIDFILE"
echo "✔ 已停止"

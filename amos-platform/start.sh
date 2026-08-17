#!/usr/bin/env bash
# ═══ AMOS 场景仿真平台 — 一键启动/重启 ═══
# 用法:
#   ./start.sh                    # 后台启动（若已在运行则先停止再重启）
#   PORT=8080 ./start.sh          # 自定义端口
#   FOREGROUND=1 ./start.sh       # 前台运行（适合 systemd/容器/远程任务）
#   ./stop.sh                     # 停止
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-5000}"
HOST="${HOST:-127.0.0.1}"
A2A_BACKEND_MODE="${A2A_BACKEND_MODE:-gateway}"
PIDFILE=".amos.pid"
LOGFILE=".amos-server.log"

# ── 1. 选择 Python：优先项目虚拟环境，其次系统 Python ──
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
  PYTHONPATH_ARG=""
elif command -v python >/dev/null 2>&1; then
  PY="python"
  PYTHONPATH_ARG="PYTHONPATH=src"
else
  PY="python3"
  PYTHONPATH_ARG="PYTHONPATH=src"
fi

# ── 2. 若已有实例在运行，先停掉（重启语义）──
#    优先读 PID 文件（仅保留仍存活的）；若均不存活（PID 文件缺失或已失效，
#    如手动 nohup 启动），按进程名 pgrep 兜底检测
#    注意：匹配到多个时全部停止（可能残留 nohup 包装进程）
OLD_PIDS=""
for p in $(cat "$PIDFILE" 2>/dev/null || true); do
  kill -0 "$p" 2>/dev/null && OLD_PIDS="$OLD_PIDS $p"
done
if [ -z "$OLD_PIDS" ]; then
  OLD_PIDS="$(pgrep -f "python.*amos_platform.api.app_factory" || true)"
fi
if [ -n "$OLD_PIDS" ]; then
  for OLD_PID in $OLD_PIDS; do
    kill -0 "$OLD_PID" 2>/dev/null || continue
    echo "▶ 检测到旧实例 (pid $OLD_PID)，正在停止 ..."
    kill "$OLD_PID"
  done
  for _ in $(seq 1 20); do
    ALIVE=0
    for OLD_PID in $OLD_PIDS; do
      kill -0 "$OLD_PID" 2>/dev/null && ALIVE=1
    done
    [ "$ALIVE" = 0 ] && break
    sleep 0.3
  done
  for OLD_PID in $OLD_PIDS; do
    if kill -0 "$OLD_PID" 2>/dev/null; then
      echo "✘ 旧实例 (pid $OLD_PID) 未能停止，请手动检查"; exit 1
    fi
  done
fi
rm -f "$PIDFILE"

# ── 3. 启动 ──
if [ "${FOREGROUND:-0}" = "1" ]; then
  echo "$$" > "$PIDFILE"
  echo "▶ 前台启动服务 (${PY})，监听: ${HOST}:${PORT}，后端入口: ${A2A_BACKEND_MODE}"
  exec env $PYTHONPATH_ARG A2A_BACKEND_MODE="$A2A_BACKEND_MODE" "$PY" -m amos_platform.api.app_factory \
    --host "$HOST" --port "$PORT"
fi

echo "▶ 启动服务 (${PY}) ..."
nohup env $PYTHONPATH_ARG A2A_BACKEND_MODE="$A2A_BACKEND_MODE" "$PY" -m amos_platform.api.app_factory \
  --host "$HOST" --port "$PORT" > "$LOGFILE" 2>&1 < /dev/null &
echo $! > "$PIDFILE"

# ── 4. 等待就绪（最多 15 秒）──
echo -n "▶ 等待服务就绪 "
sleep 1
if ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo ""
  echo "✘ 新实例启动后即退出（可能端口被占），最近日志如下："
  tail -20 "$LOGFILE" || true
  exit 1
fi
for _ in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${PORT}/" >/dev/null 2>&1; then
    echo ""
    echo "✔ 服务已启动: http://127.0.0.1:${PORT}/  (pid $(cat "$PIDFILE"))"
    echo "  监听地址: ${HOST}:${PORT}"
    echo "  后端入口: ${A2A_BACKEND_MODE}"
    echo "  日志: ${LOGFILE}    停止: ./stop.sh"
    exit 0
  fi
  echo -n "."
  sleep 0.5
done

echo ""
echo "✘ 服务启动超时，最近日志如下："
tail -20 "$LOGFILE" || true
exit 1

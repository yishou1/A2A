#!/usr/bin/env bash
# ═══ AMOS 场景仿真平台 — 一键安装环境（新电脑/新终端通用）═══
# 用法: ./setup.sh
# 要求: 已安装 Python 3.10+（检查: python3 --version）
set -euo pipefail
cd "$(dirname "$0")"

# ── 1. 创建虚拟环境（若已存在则复用）──
if [ ! -d .venv ]; then
  echo "▶ 创建虚拟环境 .venv ..."
  python3 -m venv .venv
else
  echo "▶ 复用已有虚拟环境 .venv"
fi

# ── 2. 安装项目（可编辑模式，含运行与测试依赖）──
echo "▶ 安装项目与依赖（首次需联网，约 1-2 分钟）..."
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -e '.[dev]' -q

# ── 3. 验证 ──
echo "▶ 验证安装 ..."
.venv/bin/python -c "import amos_platform, flask, waitress; print('   amos_platform OK →', amos_platform.__file__)"

echo ""
echo "✔ 环境就绪！启动:"
echo "   ./start.sh          # 一键启动（后台运行）"
echo "   ./stop.sh           # 停止"
echo "   .venv/bin/python -m pytest -q   # 跑测试"

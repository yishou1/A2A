#!/usr/bin/env bash
# =============================================================================
# AlgoLib 四类接口 Demo 启动脚本
# 用法：bash demo/run_demo.sh [--rebuild]
#   --rebuild  强制重新 cmake configure（默认复用已有 build 目录）
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUILD_DIR="${REPO_ROOT}/build"
DEMO_DIR="${REPO_ROOT}/demo"
OUTPUT_FILE="${DEMO_DIR}/demo_output.json"

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║         AlgoLib Demo 编译 & 运行脚本                       ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo "  仓库根目录：${REPO_ROOT}"
echo "  构建目录：  ${BUILD_DIR}"
echo "  输出报告：  ${OUTPUT_FILE}"
echo ""

# ─── 检查 CMake ────────────────────────────────────────────────────────────────
if ! command -v cmake &>/dev/null; then
    echo "[ERROR] cmake 未找到，请先安装 cmake（brew install cmake）"
    exit 1
fi

# ─── 是否强制重新 configure ───────────────────────────────────────────────────
REBUILD=0
for arg in "$@"; do
    [[ "$arg" == "--rebuild" ]] && REBUILD=1
done

if [[ $REBUILD -eq 1 ]]; then
    echo "[INFO] --rebuild 已指定，清理 build 目录..."
    rm -rf "${BUILD_DIR}"
fi

# ─── CMake configure（若 build 目录不存在或 --rebuild）────────────────────────
if [[ ! -f "${BUILD_DIR}/CMakeCache.txt" ]]; then
    echo "[INFO] 执行 cmake configure..."
    cmake -S "${REPO_ROOT}" -B "${BUILD_DIR}" \
        -DCMAKE_BUILD_TYPE=Release \
        -DALGOLIB_BUILD_TESTS=OFF \
        -DALGOLIB_WITH_ONNXRUNTIME=OFF
    echo "[INFO] cmake configure 完成"
fi

# ─── 编译 algolib_demo ────────────────────────────────────────────────────────
echo "[INFO] 编译 algolib_demo..."
cmake --build "${BUILD_DIR}" --target algolib_demo --config Release -- -j"$(nproc 2>/dev/null || sysctl -n hw.logicalcpu)"
echo "[INFO] 编译完成"

# ─── 运行 Demo ────────────────────────────────────────────────────────────────
DEMO_BIN="${BUILD_DIR}/algolib_demo"
if [[ ! -x "${DEMO_BIN}" ]]; then
    echo "[ERROR] 可执行文件不存在：${DEMO_BIN}"
    exit 1
fi

echo ""
echo "[INFO] 启动 Demo..."
echo "──────────────────────────────────────────────────────────────"

# 运行 demo，实时打印输出，同时将输出保存到临时日志
DEMO_LOG="${DEMO_DIR}/demo_run.log"
set +e
"${DEMO_BIN}" 2>&1 | tee "${DEMO_LOG}"
DEMO_EXIT=${PIPESTATUS[0]}
set -e

echo "──────────────────────────────────────────────────────────────"
echo ""

if [[ $DEMO_EXIT -eq 0 ]]; then
    echo "✅  Demo 全部通过！"
else
    echo "❌  Demo 存在失败项，exit code=${DEMO_EXIT}"
fi

# ─── 输出报告路径 ──────────────────────────────────────────────────────────────
if [[ -f "${OUTPUT_FILE}" ]]; then
    echo ""
    echo "📄  JSON 报告已生成：${OUTPUT_FILE}"
    echo "    运行以下命令查看汇总："
    echo "    python3 -c \""
    echo "      import json, sys"
    echo "      r = json.load(open('${OUTPUT_FILE}'))"
    echo "      s = r.get('summary', {})"
    echo "      [print(f'  {k}: {v[\"passed\"]}/{v[\"total\"]} passed') for k, v in s.items() if isinstance(v, dict)]"
    echo "      print('all_passed:', s.get('all_passed', '?'))"
    echo "    \""
fi

exit ${DEMO_EXIT}

#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

INDEX_DIR="$REPO_ROOT/outputs/newport_roe_handbook_2022/indexes/newport-roe-handbook-2022-v1/qwen3-embedding:0.6b"
GRAPH_PICKLE="$INDEX_DIR/graph.pickle"
OPENIE_JSON="$REPO_ROOT/outputs/newport_roe_handbook_2022/openie/newport-roe-handbook-2022-v1/openie_results.json"
CONVERTER="$REPO_ROOT/scripts/analysis/graph_pickle_to_html.py"

HOST="127.0.0.1"
PORT="7860"
MAX_NODES="0"
FOCUS_HOPS="2"
MIN_EDGE_WEIGHT="0.0"
OUTPUT_ARG="graph.html"
GENERATE_ONLY=false
NO_CHUNKS=false
FOCUS_TERMS=()

usage() {
    cat <<'EOF'
用法：
  scripts/run_newport_graph_visualization.sh [选项]

默认行为：
  生成当前 Newport ROE 图谱的 graph.html，并在
  http://127.0.0.1:7860/graph.html 启动静态页面服务。

选项：
  --host HOST              HTTP 监听地址，默认 127.0.0.1
  --port PORT              HTTP 端口，默认 7860
  --max-nodes N            页面可调节点上限，默认 0（加载全部节点）
  --focus TEXT             只关注包含关键词的节点，可重复指定
  --focus-hops N           焦点节点向外扩展跳数，默认 2
  --min-edge-weight VALUE  最小边权重，默认 0.0
  --no-chunks              不显示文档分块节点
  --output FILE            输出文件名或绝对路径，默认 graph.html
  --generate-only          仅生成 HTML，不启动 HTTP 服务
  -h, --help               显示帮助

示例：
  # 加载全部节点，页面默认先显示 300 个，可用滑块扩展到全量
  scripts/run_newport_graph_visualization.sh

  # 生成 Warning Shots 两跳局部图
  scripts/run_newport_graph_visualization.sh \
    --focus "Warning Shots" \
    --max-nodes 180 \
    --output warning_shots_graph.html

  # 只生成 HTML
  scripts/run_newport_graph_visualization.sh --generate-only
EOF
}

require_value() {
    local option="$1"
    local value="${2:-}"
    if [[ -z "$value" ]]; then
        echo "错误：$option 需要参数。" >&2
        usage >&2
        exit 2
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)
            require_value "$1" "${2:-}"
            HOST="$2"
            shift 2
            ;;
        --port)
            require_value "$1" "${2:-}"
            PORT="$2"
            shift 2
            ;;
        --max-nodes)
            require_value "$1" "${2:-}"
            MAX_NODES="$2"
            shift 2
            ;;
        --focus)
            require_value "$1" "${2:-}"
            FOCUS_TERMS+=("$2")
            shift 2
            ;;
        --focus-hops)
            require_value "$1" "${2:-}"
            FOCUS_HOPS="$2"
            shift 2
            ;;
        --min-edge-weight)
            require_value "$1" "${2:-}"
            MIN_EDGE_WEIGHT="$2"
            shift 2
            ;;
        --no-chunks)
            NO_CHUNKS=true
            shift
            ;;
        --output)
            require_value "$1" "${2:-}"
            OUTPUT_ARG="$2"
            shift 2
            ;;
        --generate-only)
            GENERATE_ONLY=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "错误：未知参数 $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    PYTHON="$REPO_ROOT/.venv/bin/python"
else
    PYTHON="python"
fi

for required_file in "$GRAPH_PICKLE" "$OPENIE_JSON" "$CONVERTER"; do
    if [[ ! -f "$required_file" ]]; then
        echo "错误：缺少必需文件：$required_file" >&2
        echo "请先完成 Newport ROE 文档的图谱索引构建。" >&2
        exit 1
    fi
done

if [[ "$OUTPUT_ARG" = /* ]]; then
    OUTPUT_HTML="$OUTPUT_ARG"
else
    OUTPUT_HTML="$INDEX_DIR/$OUTPUT_ARG"
fi

CONVERT_ARGS=(
    "$CONVERTER"
    "$GRAPH_PICKLE"
    --openie-json "$OPENIE_JSON"
    --max-nodes "$MAX_NODES"
    --focus-hops "$FOCUS_HOPS"
    --min-edge-weight "$MIN_EDGE_WEIGHT"
    --output "$OUTPUT_HTML"
)

for focus_term in "${FOCUS_TERMS[@]}"; do
    CONVERT_ARGS+=(--focus "$focus_term")
done

if [[ "$NO_CHUNKS" == true ]]; then
    CONVERT_ARGS+=(--no-chunks)
fi

echo "正在生成 Newport ROE 图谱页面……"
"$PYTHON" "${CONVERT_ARGS[@]}"

OUTPUT_HTML="$(cd -- "$(dirname -- "$OUTPUT_HTML")" && pwd)/$(basename -- "$OUTPUT_HTML")"
SERVE_DIR="$(dirname -- "$OUTPUT_HTML")"
PAGE_NAME="$(basename -- "$OUTPUT_HTML")"

echo "图谱页面：$OUTPUT_HTML"

if [[ "$GENERATE_ONLY" == true ]]; then
    echo "已按 --generate-only 要求结束，未启动 HTTP 服务。"
    exit 0
fi

echo
echo "浏览器访问：http://$HOST:$PORT/$PAGE_NAME"
echo "停止服务：在当前终端按 Ctrl+C"
echo "提示：远程使用 VS Code 时，请转发端口 $PORT。"
echo

exec "$PYTHON" -m http.server "$PORT" --bind "$HOST" --directory "$SERVE_DIR"

#!/bin/bash
# Claude OpenIE 数据处理脚本
# 用途：使用 Claude 模型执行 OpenIE、Embedding、图谱构建
#
# 使用方法：
#   bash run_claude_openie.sh                      # 默认 sample 数据集
#   bash run_claude_openie.sh musique              # 指定数据集
#   bash run_claude_openie.sh hotpotqa nvidia/NV-Embed-v2  # 指定数据集和 embedding

set -e  # 遇到错误立即退出

# 配置 Claude API（必须由调用者提供，脚本不保存密钥）
: "${SYNAPSERAG_OPENIE_API_KEY:?请先设置 SYNAPSERAG_OPENIE_API_KEY}"

# 默认参数
DATASET=${1:-"sample"}  # 默认使用 sample 数据集
EMBEDDING=${2:-"nvidia/NV-Embed-v2"}  # 默认 embedding 模型

echo "=========================================="
echo "Claude OpenIE 数据处理"
echo "=========================================="
echo "数据集: $DATASET"
echo "Embedding 模型: $EMBEDDING"
echo "LLM 模型: claude-opus-4-5-20251101"
echo "=========================================="
echo ""

# 检查虚拟环境
if ! command -v conda &> /dev/null; then
    echo "❌ 错误: 未找到 conda，请先安装 Miniconda/Anaconda"
    exit 1
fi

# 激活虚拟环境
echo "正在激活 synapserag 虚拟环境..."
source ~/miniconda3/etc/profile.d/conda.sh 2>/dev/null || source /home/yl/yl/shiheng/shiheng/miniconda3/etc/profile.d/conda.sh 2>/dev/null || true

# 检查是否成功激活
if ! conda activate synapserag 2>/dev/null; then
    echo "⚠️  警告: 无法激活 conda 环境，尝试直接使用 Python..."
    PYTHON_CMD="/home/yl/yl/shiheng/shiheng/miniconda3/envs/synapserag/bin/python"
else
    PYTHON_CMD="python"
fi

# 检查数据集文件是否存在
CORPUS_FILE="reproduce/dataset/${DATASET}_corpus.json"
if [ ! -f "$CORPUS_FILE" ]; then
    echo "❌ 错误: 数据集文件不存在: $CORPUS_FILE"
    echo "可用数据集: sample, musique, hotpotqa, 2wikimultihopqa"
    exit 1
fi

echo "✅ 找到数据集文件: $CORPUS_FILE"
echo ""

# 执行索引（OpenIE + Embedding + 图谱构建）
echo "开始执行 Claude OpenIE 处理..."
echo ""

$PYTHON_CMD main.py \
  --dataset "$DATASET" \
  --llm_name "requests/claude-opus-4-5-20251101" \
  --llm_base_url "https://www.openclaudecode.cn/v1" \
  --embedding_name "$EMBEDDING" \
  --openie_mode online \
  --force_index_from_scratch false \
  --force_openie_from_scratch false \
  --log_level INFO

EXIT_CODE=$?

echo ""
echo "=========================================="
if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ Claude OpenIE 处理完成！"
    echo ""
    echo "输出目录: outputs/$DATASET/requests_claude-opus-4-5-20251101_${EMBEDDING//\//_}"
    echo ""
    echo "生成的文件："
    echo "  - OpenIE 结果: reproduce/dataset/openie_results/"
    echo "  - LLM 缓存: outputs/$DATASET/.../llm_cache/"
    echo "  - 嵌入文件: outputs/$DATASET/.../chunk_embeddings/, entity_embeddings/, fact_embedding/"
    echo "  - 知识图谱: outputs/$DATASET/.../graph.pkl"
else
    echo "❌ 处理失败，退出码: $EXIT_CODE"
fi
echo "=========================================="

exit $EXIT_CODE

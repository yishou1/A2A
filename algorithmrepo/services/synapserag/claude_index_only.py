#!/usr/bin/env python3
"""
Claude 索引专用脚本 - 仅执行 OpenIE + Embedding + 图谱构建
不执行检索、Rerank 和 QA，完全避免 rerank 模型调用

使用方法：
    python claude_index_only.py                          # 默认 sample 数据集
    python claude_index_only.py --dataset musique        # 指定数据集
    python claude_index_only.py --dataset sample --force_openie true  # 强制重新 OpenIE
"""

import os
import json
import argparse
from datetime import datetime

from src.synapserag.SynapseRAG import SynapseRAG
from src.synapserag.utils.config_utils import BaseConfig
from src.synapserag.utils.misc_utils import string_to_bool
from src.synapserag.utils.logging_utils import setup_logging, get_logger

# Claude API 配置
CLAUDE_API_KEY = os.environ.get("SYNAPSERAG_OPENIE_API_KEY") or os.environ.get("SYNAPSERAG_OPENAI_API_KEY")
CLAUDE_BASE_URL = "https://www.openclaudecode.cn/v1"
CLAUDE_MODEL = "requests/claude-opus-4-5-20251101"


def main():
    parser = argparse.ArgumentParser(
        description="Claude 索引专用脚本 - 仅 OpenIE + Embedding + 图谱构建"
    )
    parser.add_argument('--dataset', type=str, default='sample',
                        help='数据集名称 (sample, musique, hotpotqa, 2wikimultihopqa)')
    parser.add_argument('--embedding_name', type=str, default='nvidia/NV-Embed-v2',
                        help='Embedding 模型名称')
    parser.add_argument('--force_openie', type=str, default='false',
                        help='是否强制重新运行 OpenIE (true/false)')
    parser.add_argument('--force_rebuild_graph', type=str, default='false',
                        help='是否强制重建图拓扑 (true/false)')
    parser.add_argument('--log_level', type=str, default='INFO',
                        help='日志级别 (DEBUG, INFO, WARNING, ERROR)')
    args = parser.parse_args()

    if not CLAUDE_API_KEY:
        raise RuntimeError("Set SYNAPSERAG_OPENIE_API_KEY before building the index")
    os.environ["SYNAPSERAG_OPENIE_API_KEY"] = CLAUDE_API_KEY

    dataset_name = args.dataset
    save_dir = f"outputs/{dataset_name}"

    print("=" * 70)
    print("Claude 索引专用脚本")
    print("=" * 70)
    print(f"数据集: {dataset_name}")
    print(f"OpenIE 模型: Claude Opus 4.5")
    print(f"Embedding 模型: {args.embedding_name}")
    print(f"强制重新 OpenIE: {args.force_openie}")
    print(f"强制重建图谱: {args.force_rebuild_graph}")
    print("=" * 70)
    print()

    # 加载数据集
    corpus_path = f"reproduce/dataset/{dataset_name}_corpus.json"
    if not os.path.exists(corpus_path):
        print(f"❌ 错误: 数据集文件不存在: {corpus_path}")
        print("可用数据集: sample, musique, hotpotqa, 2wikimultihopqa")
        return 1

    print(f"📖 加载数据集: {corpus_path}")
    with open(corpus_path, "r") as f:
        corpus = json.load(f)

    docs = [f"{doc['title']}\n{doc['text']}" for doc in corpus]
    print(f"✅ 加载了 {len(docs)} 个文档")
    print()

    # 配置
    config = BaseConfig(
        save_dir=save_dir,
        llm_name=CLAUDE_MODEL,
        llm_base_url=CLAUDE_BASE_URL,
        dataset=dataset_name,
        embedding_model_name=args.embedding_name,
        force_openie_from_scratch=string_to_bool(args.force_openie),
        force_rebuild_graph=string_to_bool(args.force_rebuild_graph),
        openie_mode='online',
        corpus_len=len(corpus),
    )

    # 设置日志
    run_name = f"{dataset_name}-claude-index-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    log_dir = os.path.join(save_dir, "logs")
    log_path = setup_logging(log_dir=log_dir, run_name=run_name, level=args.log_level)
    logger = get_logger(__name__)
    logger.info("日志文件: %s", log_path)

    # 初始化 SynapseRAG
    print("🚀 初始化 SynapseRAG...")
    synapserag = SynapseRAG(global_config=config)
    print()

    # 仅执行索引阶段
    print("=" * 70)
    print("开始索引阶段")
    print("=" * 70)
    print()
    print("📝 步骤 1: OpenIE（使用 Claude 提取实体和关系）")
    print("📝 步骤 2: Embedding（生成向量嵌入）")
    print("📝 步骤 3: 图谱构建（构建知识图谱）")
    print()

    try:
        # 执行索引
        synapserag.index(docs)

        print()
        print("=" * 70)
        print("✅ 索引完成！")
        print("=" * 70)
        print()

        # 显示统计信息
        print("📊 生成的文件和统计：")
        print()
        print(f"📁 输出目录: {synapserag.working_dir}")
        print()
        print("📄 OpenIE 结果:")
        openie_path = f"reproduce/dataset/openie_results/openie_{dataset_name}_results_ner_{CLAUDE_MODEL.replace('/', '_')}_3.json"
        if os.path.exists(openie_path):
            print(f"   ✓ {openie_path}")
        print()
        print("💾 Embedding 文件:")
        print(f"   ✓ Chunk embeddings: {synapserag.working_dir}/chunk_embeddings/")
        print(f"   ✓ Entity embeddings: {synapserag.working_dir}/entity_embeddings/")
        print(f"   ✓ Fact embeddings: {synapserag.working_dir}/fact_embeddings/")
        print()
        print("🕸️  知识图谱:")
        print(f"   ✓ 图谱文件: {synapserag.working_dir}/graph.pickle")
        print(f"   ✓ 节点数: {synapserag.graph.vcount()}")
        print(f"   ✓ 边数: {synapserag.graph.ecount()}")
        print()
        print("🗄️  缓存:")
        print(f"   ✓ LLM 缓存: {synapserag.working_dir}/llm_cache/")
        print()
        print("⚙️  边权重策略:")
        print(f"   ✓ 当前策略: {config.edge_weight_mode}")
        if config.edge_weight_mode == 'base':
            print("   ℹ️  使用基础统计值（原始共现次数）")
            print("   💡 如需使用高级权重策略，可以修改配置：")
            print("      - heuristic_mroaw: 启发式多维度加权")
            print("      - probabilistic_mroaw: 概率化 MROAW（推荐）")
        print()
        print("=" * 70)
        print()
        print("💡 提示:")
        print("  - OpenIE 结果已缓存，下次运行会自动复用")
        print("  - 如需重新运行 OpenIE，使用: --force_openie true")
        print("  - 如需处理其他数据集，使用: --dataset <dataset_name>")
        print()

        return 0

    except Exception as e:
        logger.error(f"索引失败: {e}", exc_info=True)
        print()
        print("=" * 70)
        print(f"❌ 索引失败: {e}")
        print("=" * 70)
        print()
        print("请检查日志文件获取详细错误信息：")
        print(f"  {log_path}")
        return 1


if __name__ == "__main__":
    exit(main())

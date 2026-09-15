#!/usr/bin/env python3
"""
Claude OpenIE 索引脚本
用途：仅执行索引阶段（OpenIE → Embedding → 图谱构建），不执行检索和 QA

使用方法：
    python run_claude_index_only.py                          # 默认 sample 数据集
    python run_claude_index_only.py --dataset musique        # 指定数据集
    python run_claude_index_only.py --dataset sample --force_openie_from_scratch true  # 强制重新 OpenIE
"""

import os
import json
import argparse
from datetime import datetime

from src.synapserag.SynapseRAG import SynapseRAG
from src.synapserag.utils.config_utils import BaseConfig, LLMEndpointConfig
from src.synapserag.utils.misc_utils import string_to_bool
from src.synapserag.utils.logging_utils import setup_logging, get_logger

# Claude API 配置
CLAUDE_API_KEY = os.environ.get("SYNAPSERAG_OPENIE_API_KEY") or os.environ.get("SYNAPSERAG_OPENAI_API_KEY")
CLAUDE_BASE_URL = "https://www.openclaudecode.cn/v1"
CLAUDE_MODEL = "requests/claude-opus-4-5-20251101"


def main():
    parser = argparse.ArgumentParser(
        description="Claude OpenIE 索引脚本 - 仅执行 OpenIE、Embedding 和图谱构建"
    )
    parser.add_argument('--dataset', type=str, default='sample',
                        help='数据集名称 (sample, musique, hotpotqa, 2wikimultihopqa)')
    parser.add_argument('--index_id', type=str, default=None,
                        help='稳定索引 ID（默认：<dataset>-qwen3-v1）')
    parser.add_argument('--embedding_name', type=str, default='qwen3-embedding:0.6b',
                        help='Embedding 模型名称')
    parser.add_argument('--embedding_base_url', type=str,
                        default=os.getenv(
                            "SYNAPSERAG_EMBEDDING_BASE_URL",
                            "http://127.0.0.1:11434/v1",
                        ),
                        help='本地 OpenAI-compatible embedding /v1 地址')
    parser.add_argument('--force_openie_from_scratch', type=str, default='false',
                        help='是否强制重新运行 OpenIE (true/false)')
    parser.add_argument('--force_index_from_scratch', type=str, default='false',
                        help='是否强制重建索引 (true/false)')
    parser.add_argument('--log_level', type=str, default='INFO',
                        help='日志级别 (DEBUG, INFO, WARNING, ERROR)')
    parser.add_argument('--log_dir', type=str, default=None,
                        help='日志目录（默认：<save_dir>/logs）')
    args = parser.parse_args()

    dataset_name = args.dataset
    index_id = args.index_id or f"{dataset_name}-qwen3-v1"
    save_dir = f"outputs/{dataset_name}"
    print("=" * 60)
    print("Claude OpenIE 索引脚本")
    print("=" * 60)
    print(f"数据集: {dataset_name}")
    print(f"LLM 模型: {CLAUDE_MODEL}")
    print(f"Embedding 模型: {args.embedding_name}")
    print(f"强制重新 OpenIE: {args.force_openie_from_scratch}")
    print(f"强制重建索引: {args.force_index_from_scratch}")
    print("=" * 60)
    print()

    # 加载数据集
    corpus_path = f"reproduce/dataset/{dataset_name}_corpus.json"
    if not os.path.exists(corpus_path):
        print(f"❌ 错误: 数据集文件不存在: {corpus_path}")
        print("可用数据集: sample, musique, hotpotqa, 2wikimultihopqa")
        return 1

    print(f"加载数据集: {corpus_path}")
    with open(corpus_path, "r") as f:
        corpus = json.load(f)

    docs = [f"{doc['title']}\n{doc['text']}" for doc in corpus]
    print(f"✅ 加载了 {len(docs)} 个文档")
    print()

    # 配置
    config = BaseConfig(
        save_dir=save_dir,
        index_id=index_id,
        runtime_stage="build-index",
        openie_llm=LLMEndpointConfig(
            model_name=CLAUDE_MODEL,
            base_url=CLAUDE_BASE_URL,
            api_key_env="SYNAPSERAG_OPENIE_API_KEY",
        ),
        dataset=dataset_name,
        embedding_model_name=args.embedding_name,
        embedding_base_url=args.embedding_base_url,
        force_openie_from_scratch=string_to_bool(args.force_openie_from_scratch),
        force_index_from_scratch=string_to_bool(args.force_index_from_scratch),
        openie_mode='online',
        corpus_len=len(corpus),
    )

    # 设置日志
    run_name = f"{dataset_name}-claude-index-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    log_dir = args.log_dir or os.path.join(save_dir, "logs")
    log_path = setup_logging(log_dir=log_dir, run_name=run_name, level=args.log_level)
    logger = get_logger(__name__)
    logger.info("写入日志到: %s", log_path)

    # 初始化 SynapseRAG
    print("初始化 SynapseRAG...")
    synapserag = SynapseRAG(global_config=config)
    print()

    # 仅执行索引
    print("=" * 60)
    print("开始索引阶段（OpenIE → Embedding → 图谱构建）")
    print("=" * 60)
    print()

    try:
        report = synapserag.build_index(docs, allow_openie_calls=False)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print()
        print("=" * 60)
        print("✅ 索引完成！")
        print("=" * 60)
        print()
        print(f"输出目录: {save_dir}")
        print()
        print("生成的文件：")
        print(f"  - OpenIE 结果: {synapserag.openie_results_path}")
        print(f"  - LLM 缓存: {save_dir}/llm_cache/")
        print(f"  - 嵌入文件: {save_dir}/chunk_embeddings/, entity_embeddings/, fact_embedding/")
        print(f"  - 知识图谱: {synapserag.working_dir}/graph.pickle")
        print("=" * 60)
        return 0

    except Exception as e:
        logger.error(f"索引失败: {e}", exc_info=True)
        print()
        print("=" * 60)
        print(f"❌ 索引失败: {e}")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    exit(main())

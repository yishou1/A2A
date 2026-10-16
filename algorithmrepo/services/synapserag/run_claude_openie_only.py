#!/usr/bin/env python3
"""
Claude OpenIE 脚本（仅 OpenIE，不做嵌入）
用途：只执行 OpenIE 阶段，不计算嵌入，节省时间和资源

使用方法：
    python run_claude_openie_only.py                              # 默认 musique 数据集
    python run_claude_openie_only.py --dataset sample             # 指定数据集
    python run_claude_openie_only.py --force_openie_from_scratch true  # 强制重新 OpenIE

完成后运行嵌入和图构建：
    python run_claude_index_only.py --dataset musique
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
        description="Claude OpenIE 脚本 - 仅执行 OpenIE，不做嵌入"
    )
    parser.add_argument('--dataset', type=str, default='musique',
                        help='数据集名称 (sample, musique, hotpotqa, 2wikimultihopqa)')
    parser.add_argument('--index_id', type=str, default=None,
                        help='稳定索引 ID（默认：<dataset>-qwen3-v1）')
    parser.add_argument('--force_openie_from_scratch', type=str, default='false',
                        help='是否强制重新运行 OpenIE (true/false)')
    parser.add_argument('--log_level', type=str, default='INFO',
                        help='日志级别 (DEBUG, INFO, WARNING, ERROR)')
    parser.add_argument('--log_dir', type=str, default=None,
                        help='日志目录（默认：<save_dir>/logs）')
    args = parser.parse_args()

    if not CLAUDE_API_KEY:
        raise RuntimeError("Set SYNAPSERAG_OPENIE_API_KEY before running OpenIE")
    os.environ["SYNAPSERAG_OPENIE_API_KEY"] = CLAUDE_API_KEY

    dataset_name = args.dataset
    index_id = args.index_id or f"{dataset_name}-qwen3-v1"
    save_dir = f"outputs/{dataset_name}"

    print("=" * 60)
    print("Claude OpenIE 脚本（仅 OpenIE，不做嵌入）")
    print("=" * 60)
    print(f"数据集: {dataset_name}")
    print(f"LLM 模型: {CLAUDE_MODEL}")
    print(f"强制重新 OpenIE: {args.force_openie_from_scratch}")
    print("=" * 60)
    print()

    # 加载数据集
    corpus_path = f"reproduce/dataset/{dataset_name}_corpus.json"
    if not os.path.exists(corpus_path):
        print(f"错误: 数据集文件不存在: {corpus_path}")
        print("可用数据集: sample, musique, hotpotqa, 2wikimultihopqa")
        return 1

    print(f"加载数据集: {corpus_path}")
    with open(corpus_path, "r") as f:
        corpus = json.load(f)

    docs = [f"{doc['title']}\n{doc['text']}" for doc in corpus]
    print(f"加载了 {len(docs)} 个文档")
    print()

    # OpenIE-only 阶段不初始化 embedding 或 QA。
    config = BaseConfig(
        save_dir=save_dir,
        index_id=index_id,
        runtime_stage="openie",
        openie_llm=LLMEndpointConfig(
            model_name=CLAUDE_MODEL,
            base_url=CLAUDE_BASE_URL,
            api_key_env="SYNAPSERAG_OPENIE_API_KEY",
            temperature=0.0,
            max_tokens=2048,
        ),
        dataset=dataset_name,
        force_openie_from_scratch=string_to_bool(args.force_openie_from_scratch),
        force_index_from_scratch=False,
        save_openie=True,  # 确保保存 OpenIE 结果
        corpus_len=len(corpus),
    )

    # 设置日志
    run_name = f"{dataset_name}-claude-openie-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    log_dir = args.log_dir or os.path.join(save_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = setup_logging(log_dir=log_dir, run_name=run_name, level=args.log_level)
    logger = get_logger(__name__)
    logger.info("写入日志到: %s", log_path)

    print("初始化 SynapseRAG...")
    synapserag = SynapseRAG(global_config=config)
    print()

    print("=" * 60)
    print("开始 OpenIE 阶段（仅提取实体和关系，不计算嵌入）")
    print("=" * 60)
    print()

    try:
        report = synapserag.extract_openie(
            docs,
            force=string_to_bool(args.force_openie_from_scratch),
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print()
        print(f"输出文件: {synapserag.openie_results_path}")
        print()
        print("下一步：运行嵌入和图构建")
        print(f"  python run_claude_index_only.py --dataset {dataset_name} --index_id {index_id}")
        print("=" * 60)

        return 0

    except Exception as e:
        logger.error(f"OpenIE 失败: {e}", exc_info=True)
        print()
        print("=" * 60)
        print(f"OpenIE 失败: {e}")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    exit(main())

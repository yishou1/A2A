#!/usr/bin/env python3
"""
使用 Claude 模型进行 OpenIE（实体和关系抽取）的索引脚本。

此脚本仅执行索引阶段（OpenIE + 嵌入 + 图谱构建），不执行检索和问答。
后续可以复用生成的索引数据进行检索和问答实验。

使用方法:
    # 1. 设置 API 密钥
    export ANTHROPIC_API_KEY="your-anthropic-api-key"
    # 或者使用 OpenRouter 代理（推荐，支持多种模型）
    export SYNAPSERAG_OPENAI_API_KEY="your-openrouter-api-key"

    # 2. 运行脚本
    python scripts/index_with_claude.py --dataset sample

    # 3. 使用自定义参数
    python scripts/index_with_claude.py \
        --dataset musique \
        --claude_model claude-sonnet-4-20250514 \
        --embedding_name nvidia/NV-Embed-v2 \
        --save_dir outputs/claude_index

支持的 Claude 模型:
    - anthropic/claude-sonnet-4-20250514 (推荐，性价比高)
    - anthropic/claude-3.5-sonnet (较快)
    - anthropic/claude-3-opus (最强，较贵)
    - anthropic/claude-3-haiku (最快最便宜，适合测试)
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.synapserag import SynapseRAG
from src.synapserag.utils.config_utils import BaseConfig
from src.synapserag.utils.logging_utils import setup_logging
from src.synapserag.utils.misc_utils import string_to_bool

logger = logging.getLogger(__name__)

# Claude 模型配置
CLAUDE_MODELS = {
    "claude-4-sonnet": "anthropic/claude-sonnet-4-20250514",
    "claude-3.5-sonnet": "anthropic/claude-3.5-sonnet",
    "claude-3-opus": "anthropic/claude-3-opus",
    "claude-3-haiku": "anthropic/claude-3-haiku",
    "claude-3.5-haiku": "anthropic/claude-3.5-haiku",
}

# 默认使用 OpenRouter 作为代理（支持 Claude 和其他模型）
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_CLAUDE_MODEL = "anthropic/claude-sonnet-4-20250514"


def parse_args():
    parser = argparse.ArgumentParser(
        description="使用 Claude 模型进行 OpenIE 索引",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    # 数据集配置
    parser.add_argument('--dataset', type=str, required=True,
                        help='数据集名称（如 sample, musique, hotpotqa 等）')
    parser.add_argument('--corpus_path', type=str, default=None,
                        help='自定义语料库路径（默认: reproduce/dataset/<dataset>_corpus.json）')

    # Claude 模型配置
    parser.add_argument('--claude_model', type=str, default=DEFAULT_CLAUDE_MODEL,
                        help=f'Claude 模型名称（默认: {DEFAULT_CLAUDE_MODEL}）')
    parser.add_argument('--llm_base_url', type=str, default=DEFAULT_BASE_URL,
                        help=f'LLM API base URL（默认: {DEFAULT_BASE_URL}）')

    # 嵌入模型配置
    parser.add_argument('--embedding_name', type=str, default='nvidia/NV-Embed-v2',
                        help='嵌入模型名称（默认: nvidia/NV-Embed-v2）')
    parser.add_argument('--embedding_batch_size', type=int, default=8,
                        help='嵌入批处理大小（默认: 8）')

    # 输出配置
    parser.add_argument('--save_dir', type=str, default='outputs',
                        help='保存目录（默认: outputs）')
    parser.add_argument('--log_dir', type=str, default=None,
                        help='日志目录（默认: <save_dir>/logs）')
    parser.add_argument('--log_level', type=str, default='INFO',
                        help='日志级别（默认: INFO）')

    # 索引控制
    parser.add_argument('--force_index_from_scratch', type=str, default='false',
                        help='强制从头构建索引，忽略所有缓存（默认: false）')
    parser.add_argument('--force_openie_from_scratch', type=str, default='false',
                        help='强制重新运行 OpenIE，忽略已有结果（默认: false）')
    parser.add_argument('--force_rebuild_graph', type=str, default='false',
                        help='强制重建图拓扑（默认: false）')

    # 高级配置
    parser.add_argument('--max_new_tokens', type=int, default=2048,
                        help='LLM 最大生成 token 数（默认: 2048）')
    parser.add_argument('--temperature', type=float, default=0.0,
                        help='LLM 采样温度（默认: 0.0）')
    parser.add_argument('--graph_type', type=str,
                        default='facts_and_sim_passage_node_unidirectional',
                        help='图类型（默认: facts_and_sim_passage_node_unidirectional）')

    # 边权重策略
    parser.add_argument('--edge_weight_mode', type=str, default='base',
                        choices=['uniform', 'base', 'heuristic_mroaw', 'probabilistic_mroaw'],
                        help='边权重计算策略（默认: base）')

    return parser.parse_args()


def load_corpus(dataset: str, corpus_path: str = None) -> list:
    """加载语料库"""
    if corpus_path is None:
        corpus_path = f"reproduce/dataset/{dataset}_corpus.json"

    corpus_path = Path(corpus_path)
    if not corpus_path.exists():
        raise FileNotFoundError(f"语料库文件不存在: {corpus_path}")

    logger.info(f"加载语料库: {corpus_path}")
    with open(corpus_path, "r", encoding="utf-8") as f:
        corpus = json.load(f)

    # 转换为文档列表
    docs = []
    for doc in corpus:
        if isinstance(doc, dict):
            title = doc.get('title', '')
            text = doc.get('text', '')
            docs.append(f"{title}\n{text}" if title else text)
        else:
            docs.append(str(doc))

    logger.info(f"加载了 {len(docs)} 个文档")
    return docs, len(corpus)


def main():
    args = parse_args()

    # 检查 API 密钥
    api_key = os.environ.get('SYNAPSERAG_OPENAI_API_KEY') or \
              os.environ.get('OPENROUTER_API_KEY') or \
              os.environ.get('ANTHROPIC_API_KEY')

    if not api_key:
        print("错误: 未设置 API 密钥！")
        print("请设置以下环境变量之一:")
        print("  export SYNAPSERAG_OPENAI_API_KEY='your-key'  # OpenRouter 推荐")
        print("  export OPENROUTER_API_KEY='your-key'")
        print("  export ANTHROPIC_API_KEY='your-key'")
        sys.exit(1)

    # 构建保存目录
    dataset_name = args.dataset
    save_dir = args.save_dir
    if save_dir == 'outputs':
        # 使用 claude 标识区分
        model_short = args.claude_model.split('/')[-1]
        save_dir = f"{save_dir}/{dataset_name}_claude"

    # 设置日志
    log_dir = args.log_dir or os.path.join(save_dir, "logs")
    run_name = f"index-claude-{dataset_name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    log_path = setup_logging(log_dir=log_dir, run_name=run_name, level=args.log_level)
    logger.info(f"日志文件: {log_path}")

    # 加载语料库
    docs, corpus_len = load_corpus(dataset_name, args.corpus_path)

    # 解析布尔参数
    force_index = string_to_bool(args.force_index_from_scratch)
    force_openie = string_to_bool(args.force_openie_from_scratch)
    force_graph = string_to_bool(args.force_rebuild_graph)

    # 创建配置
    logger.info("=" * 60)
    logger.info("配置信息:")
    logger.info(f"  数据集: {dataset_name}")
    logger.info(f"  Claude 模型: {args.claude_model}")
    logger.info(f"  LLM Base URL: {args.llm_base_url}")
    logger.info(f"  嵌入模型: {args.embedding_name}")
    logger.info(f"  保存目录: {save_dir}")
    logger.info(f"  边权重策略: {args.edge_weight_mode}")
    logger.info(f"  文档数量: {corpus_len}")
    logger.info("=" * 60)

    config = BaseConfig(
        # 保存配置
        save_dir=save_dir,
        dataset=dataset_name,

        # Claude LLM 配置
        llm_base_url=args.llm_base_url,
        llm_name=args.claude_model,
        max_new_tokens=args.max_new_tokens,
        temperature=args.temperature,

        # 嵌入模型配置
        embedding_model_name=args.embedding_name,
        embedding_batch_size=args.embedding_batch_size,

        # 索引控制
        force_index_from_scratch=force_index,
        force_openie_from_scratch=force_openie,
        force_rebuild_graph=force_graph,

        # 图配置
        graph_type=args.graph_type,
        edge_weight_mode=args.edge_weight_mode,

        # OpenIE 配置
        openie_mode='online',  # Claude 只支持在线模式
        save_openie=True,      # 保存 OpenIE 结果以便复用

        # 其他
        corpus_len=corpus_len,

        # 禁用 Agentic PPR（仅索引阶段）
        use_agentic_ppr_reset=False,
    )

    # 创建 SynapseRAG 实例
    logger.info("初始化 SynapseRAG...")
    synapserag = SynapseRAG(global_config=config)

    # 执行索引
    logger.info("开始索引（OpenIE + 嵌入 + 图谱构建）...")
    start_time = datetime.now()

    synapserag.index(docs)

    end_time = datetime.now()
    duration = end_time - start_time

    # 输出结果摘要
    logger.info("=" * 60)
    logger.info("索引完成！")
    logger.info(f"  耗时: {duration}")
    logger.info(f"  保存目录: {save_dir}")
    logger.info("")
    logger.info("生成的文件:")
    logger.info(f"  - {save_dir}/chunk_embeddings/      # 文档块嵌入")
    logger.info(f"  - {save_dir}/entity_embeddings/    # 实体嵌入")
    logger.info(f"  - {save_dir}/fact_embedding/       # 事实嵌入")
    logger.info(f"  - {save_dir}/graph.pkl             # 知识图谱")
    logger.info(f"  - reproduce/dataset/openie_results/openie_{dataset_name}_results_ner_{args.claude_model.replace('/', '_')}_3.json  # OpenIE 结果")
    logger.info("")
    logger.info("后续使用:")
    logger.info(f"  可以使用 main.py 加载此索引进行检索和问答实验")
    logger.info(f"  python main.py --dataset {dataset_name} --llm_name <其他LLM> --save_dir {save_dir}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
使用 Claude 模型进行 OpenIE 的快速演示脚本。

使用前请设置环境变量:
    export SYNAPSERAG_OPENAI_API_KEY="your-openrouter-api-key"

运行:
    python demo_claude_openie.py
"""

import os
import sys
import logging
from datetime import datetime
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.synapserag import SynapseRAG
from src.synapserag.utils.config_utils import BaseConfig
from src.synapserag.utils.logging_utils import setup_logging

# 配置
CLAUDE_MODEL = "claude-opus-4-5-20251101"  # Claude Opus 4.5
EMBEDDING_MODEL = "nvidia/NV-Embed-v2"
LLM_BASE_URL = "https://www.openclaudecode.cn/v1"
SAVE_DIR = "outputs/claude_demo"


def main():
    api_key = os.environ.get('SYNAPSERAG_OPENIE_API_KEY') or os.environ.get('SYNAPSERAG_OPENAI_API_KEY')
    if not api_key:
        raise RuntimeError("Set SYNAPSERAG_OPENIE_API_KEY before running the demo")
    os.environ['SYNAPSERAG_OPENIE_API_KEY'] = api_key

    # 设置日志
    log_path = setup_logging(
        log_dir=os.path.join(SAVE_DIR, "logs"),
        run_name=f"claude-demo-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        level="INFO"
    )

    logger = logging.getLogger(__name__)
    logger.info(f"日志文件: {log_path}")

    # 示例文档
    docs = [
        "Oliver Badman is a politician.",
        "George Rankin is a politician.",
        "Thomas Marwick is a politician.",
        "Cinderella attended the royal ball.",
        "The prince used the lost glass slipper to search the kingdom.",
        "When the slipper fit perfectly, Cinderella was reunited with the prince.",
        "Erik Hort's birthplace is Montebello.",
        "Marina is born in Minsk.",
        "Montebello is a part of Rockland County."
    ]

    # 配置
    config = BaseConfig(
        save_dir=SAVE_DIR,
        llm_base_url=LLM_BASE_URL,
        llm_name=CLAUDE_MODEL,
        embedding_model_name=EMBEDDING_MODEL,
        openie_mode='online',
        save_openie=True,
        use_agentic_ppr_reset=False,
        force_index_from_scratch=True,  # 演示时从头构建
    )

    print()
    print("=" * 60)
    print("🚀 使用 Claude 进行 OpenIE 演示")
    print("=" * 60)
    print(f"  Claude 模型: {CLAUDE_MODEL}")
    print(f"  嵌入模型: {EMBEDDING_MODEL}")
    print(f"  文档数量: {len(docs)}")
    print(f"  保存目录: {SAVE_DIR}")
    print("=" * 60)
    print()

    # 创建 SynapseRAG 实例
    logger.info("初始化 SynapseRAG...")
    synapserag = SynapseRAG(global_config=config)

    # 执行索引
    logger.info("开始索引...")
    start_time = datetime.now()

    synapserag.index(docs)

    end_time = datetime.now()
    duration = end_time - start_time

    print()
    print("=" * 60)
    print("✅ 索引完成！")
    print(f"  耗时: {duration}")
    print("=" * 60)
    print()

    # 可选：测试检索
    queries = [
        "What is George Rankin's occupation?",
        "How did Cinderella reach her happy ending?",
        "What county is Erik Hort's birthplace a part of?"
    ]

    gold_docs = [
        ["George Rankin is a politician."],
        ["Cinderella attended the royal ball.",
         "The prince used the lost glass slipper to search the kingdom.",
         "When the slipper fit perfectly, Cinderella was reunited with the prince."],
        ["Erik Hort's birthplace is Montebello.",
         "Montebello is a part of Rockland County."]
    ]

    answers = [
        ["Politician"],
        ["By going to the ball."],
        ["Rockland County"]
    ]

    print("测试检索和问答...")
    print()

    # 执行问答
    results = synapserag.rag_qa(
        queries=queries,
        gold_docs=gold_docs,
        gold_answers=answers
    )

    # 输出结果
    qa_responses = results[1] if len(results) > 1 else None
    overall_qa_result = results[-1] if len(results) > 2 else None

    print("=" * 60)
    print("📊 问答结果:")
    print("=" * 60)

    for i, query in enumerate(queries):
        print(f"\n问题 {i+1}: {query}")
        if qa_responses and i < len(qa_responses):
            print(f"  回答: {qa_responses[i]}")
        print(f"  参考答案: {answers[i]}")

    if overall_qa_result:
        print()
        print("总体指标:")
        print(f"  {overall_qa_result}")

    print()
    print("=" * 60)
    print("🎉 演示完成！")
    print()
    print("后续步骤:")
    print(f"  1. 查看生成的索引: {SAVE_DIR}/")
    print(f"  2. 对真实数据集运行:")
    print(f"     python scripts/index_with_claude.py --dataset musique")
    print("=" * 60)


if __name__ == "__main__":
    main()

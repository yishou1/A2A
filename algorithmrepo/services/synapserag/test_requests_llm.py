#!/usr/bin/env python3
"""
测试 RequestsLLM 类
验证 Claude API 集成是否正常工作
"""

import os
import sys

# Set SYNAPSERAG_OPENIE_API_KEY in the shell before running this integration test.

from src.synapserag.llm.requests_llm import RequestsLLM
from src.synapserag.utils.config_utils import BaseConfig

print("=" * 60)
print("测试 RequestsLLM 类")
print("=" * 60)
print()

# 创建配置
config = BaseConfig(
    llm_name="requests/claude-opus-4-5-20251101",
    llm_base_url="https://www.openclaudecode.cn/v1",
    save_dir="test_output",
    temperature=0,
    max_new_tokens=100
)

# 获取 API key
from src.synapserag.llm import _resolve_api_key
api_key = _resolve_api_key(config)
print(f"解析到的 API Key: {api_key[:20]}...{api_key[-10:]}" if api_key else "未找到 API Key")
print()

# 测试 1: 基本推理
print("测试 1: 基本推理")
print("-" * 60)

try:
    llm = RequestsLLM.from_experiment_config(config, api_key=api_key)
    print(f"✅ RequestsLLM 初始化成功")
    print(f"  LLM 实例 API Key: {llm.api_key[:20] if llm.api_key else 'None'}...")
    print()
    messages = [{"role": "user", "content": "用中文说 'Hello, I am Claude.'"}]

    print("发送请求...")
    response, metadata, cache_hit = llm.infer(messages)

    print(f"✅ 成功！")
    print(f"响应: {response}")
    print(f"Token 使用: {metadata.get('prompt_tokens', 0)} prompt + {metadata.get('completion_tokens', 0)} completion")
    print(f"缓存命中: {cache_hit}")
    print()

except Exception as e:
    print(f"❌ 失败: {e}")
    sys.exit(1)

# 测试 2: NER 任务（模拟 OpenIE）
print("测试 2: NER 任务（模拟 OpenIE）")
print("-" * 60)

try:
    ner_messages = [{
        "role": "user",
        "content": '''从以下文本中提取所有命名实体：

文本: "巴拉克·奥巴马（Barack Obama）于 1961 年出生在夏威夷（Hawaii）。他曾任美国第 44 任总统。"

请以 JSON 格式返回结果：
{"named_entities": ["实体1", "实体2", ...]}'''
    }]

    print("发送 NER 请求...")
    response, metadata, cache_hit = llm.infer(
        ner_messages,
        response_format={"type": "json_object"}  # 尝试 JSON 模式
    )

    print(f"✅ 成功！")
    print(f"NER 响应:\n{response}")
    print(f"Token 使用: {metadata.get('prompt_tokens', 0)} prompt + {metadata.get('completion_tokens', 0)} completion")
    print(f"缓存命中: {cache_hit}")
    print()

except Exception as e:
    print(f"⚠️  JSON 模式可能不支持，尝试普通模式...")

    try:
        response, metadata, cache_hit = llm.infer(ner_messages)
        print(f"✅ 普通模式成功！")
        print(f"NER 响应:\n{response}")
        print()
    except Exception as e2:
        print(f"❌ 失败: {e2}")
        sys.exit(1)

# 测试 3: 缓存机制
print("测试 3: 缓存机制")
print("-" * 60)

try:
    # 重复第一个请求，应该命中缓存
    messages = [{"role": "user", "content": "用中文说 'Hello, I am Claude.'"}]

    print("发送相同的请求（应该命中缓存）...")
    response, metadata, cache_hit = llm.infer(messages)

    if cache_hit:
        print(f"✅ 缓存命中！响应: {response[:50]}...")
    else:
        print(f"⚠️  未命中缓存（可能是缓存配置问题）")
    print()

except Exception as e:
    print(f"❌ 失败: {e}")
    sys.exit(1)

# 测试 4: 三元组提取（模拟 OpenIE）
print("测试 4: 三元组提取（模拟 OpenIE）")
print("-" * 60)

try:
    triple_messages = [{
        "role": "user",
        "content": '''从以下文本中提取关系三元组：

文本: "巴拉克·奥巴马出生在夏威夷。"
命名实体: ["巴拉克·奥巴马", "夏威夷"]

请以 JSON 格式返回结果：
{"triples": [["头实体", "关系", "尾实体"], ...]}'''
    }]

    print("发送三元组提取请求...")
    response, metadata, cache_hit = llm.infer(triple_messages)

    print(f"✅ 成功！")
    print(f"三元组响应:\n{response}")
    print()

except Exception as e:
    print(f"❌ 失败: {e}")
    sys.exit(1)

print("=" * 60)
print("✅ 所有测试通过！RequestsLLM 类工作正常。")
print("=" * 60)
print()
print("下一步：运行完整的 OpenIE 流程")
print("命令: bash run_claude_openie.sh sample")

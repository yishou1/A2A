#!/usr/bin/env python3
"""
测试 OpenAI SDK 与 Claude API 的兼容性
"""

from openai import OpenAI
import os

# Claude API 配置
LLM_BASE_URL = "https://www.openclaudecode.cn/v1"
API_KEY = os.environ.get("SYNAPSERAG_OPENIE_API_KEY") or os.environ.get("SYNAPSERAG_OPENAI_API_KEY", "")
CLAUDE_MODEL = "claude-opus-4-5-20251101"

print("=" * 60)
print("测试 OpenAI SDK 不同初始化方式")
print("=" * 60)

# 方法 1: 环境变量方式
print("\n方法 1: 使用环境变量 SYNAPSERAG_OPENAI_API_KEY")
print("-" * 60)
os.environ["SYNAPSERAG_OPENAI_API_KEY"] = API_KEY

try:
    client = OpenAI(
        base_url=LLM_BASE_URL,
        api_key=API_KEY  # 显式传入
    )

    response = client.chat.completions.create(
        model=CLAUDE_MODEL,
        messages=[{"role": "user", "content": "Say hello in Chinese"}],
        temperature=0,
        max_tokens=50
    )

    print(f"✅ 成功！响应: {response.choices[0].message.content}")
    print(f"Token 使用: {response.usage.total_tokens}")

except Exception as e:
    print(f"❌ 失败: {type(e).__name__}: {e}")

# 方法 2: 测试带 response_format 的 JSON 模式
print("\n\n方法 2: 测试 JSON 模式（OpenIE 需要）")
print("-" * 60)

try:
    client = OpenAI(
        base_url=LLM_BASE_URL,
        api_key=API_KEY
    )

    response = client.chat.completions.create(
        model=CLAUDE_MODEL,
        messages=[{
            "role": "user",
            "content": '提取命名实体，返回 JSON: "巴拉克·奥巴马出生于夏威夷"\n格式: {"named_entities": ["实体1", ...]}'
        }],
        temperature=0,
        max_tokens=100,
        response_format={"type": "json_object"}
    )

    print(f"✅ JSON 模式成功！响应:\n{response.choices[0].message.content}")

except Exception as e:
    print(f"❌ JSON 模式失败: {type(e).__name__}: {e}")
    print("   可能需要移除 response_format 参数")

# 方法 3: 不使用 response_format
print("\n\n方法 3: 不使用 JSON 模式（纯文本提示）")
print("-" * 60)

try:
    client = OpenAI(
        base_url=LLM_BASE_URL,
        api_key=API_KEY
    )

    response = client.chat.completions.create(
        model=CLAUDE_MODEL,
        messages=[{
            "role": "user",
            "content": '从以下文本提取命名实体，以 JSON 格式返回：\n"巴拉克·奥巴马出生于夏威夷"\n\n请返回 JSON 格式：{"named_entities": ["实体1", "实体2", ...]}'
        }],
        temperature=0,
        max_tokens=150
    )

    print(f"✅ 无 JSON 模式成功！响应:\n{response.choices[0].message.content}")

except Exception as e:
    print(f"❌ 失败: {type(e).__name__}: {e}")

print("\n" + "=" * 60)
print("总结：")
print("- 如果方法 1 失败，说明 OpenAI SDK 有兼容性问题")
print("- 如果方法 2 失败，需要在 OpenIE 中移除 response_format")
print("- 如果方法 3 成功，可以使用纯文本提示代替 JSON 模式")
print("=" * 60)

#!/usr/bin/env python3
"""
调试 OpenAI SDK 的请求细节
"""

import os
import httpx
from openai import OpenAI

# Claude API 配置
LLM_BASE_URL = "https://www.openclaudecode.cn/v1"
API_KEY = os.environ.get("SYNAPSERAG_OPENIE_API_KEY") or os.environ.get("SYNAPSERAG_OPENAI_API_KEY", "")
CLAUDE_MODEL = "claude-opus-4-5-20251101"

print("=" * 60)
print("调试 OpenAI SDK 请求")
print("=" * 60)

# 测试不同的 User-Agent
user_agents = [
    None,  # 默认
    "Custom-Client/1.0",  # 自定义
]

for i, ua in enumerate(user_agents, 1):
    print(f"\n测试 {i}: User-Agent = {ua or '(默认)'}")
    print("-" * 60)

    try:
        if ua:
            # 创建自定义 httpx 客户端
            http_client = httpx.Client(
                headers={"User-Agent": ua},
                timeout=30.0
            )
            client = OpenAI(
                base_url=LLM_BASE_URL,
                api_key=API_KEY,
                http_client=http_client
            )
        else:
            client = OpenAI(
                base_url=LLM_BASE_URL,
                api_key=API_KEY,
                timeout=30.0
            )

        response = client.chat.completions.create(
            model=CLAUDE_MODEL,
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=10
        )

        print(f"✅ 成功！响应: {response.choices[0].message.content}")
        break

    except Exception as e:
        print(f"❌ 失败: {type(e).__name__}: {e}")

print("\n" + "=" * 60)

#!/usr/bin/env python3
"""
Claude API 详细调试脚本
"""

import os
import requests
import json

# Claude API 配置
LLM_BASE_URL = "https://www.openclaudecode.cn/v1"
API_KEY = os.environ.get("SYNAPSERAG_OPENIE_API_KEY") or os.environ.get("SYNAPSERAG_OPENAI_API_KEY", "")
CLAUDE_MODEL = "claude-opus-4-5-20251101"

print("=" * 60)
print("Claude API 详细调试")
print("=" * 60)
print(f"API Base URL: {LLM_BASE_URL}")
print(f"模型: {CLAUDE_MODEL}")
print(f"API Key (部分): {API_KEY[:20]}...{API_KEY[-10:]}")
print()

# 方法 1: 使用 requests 直接测试
print("方法 1: 使用 requests 库直接请求")
print("-" * 60)

url = f"{LLM_BASE_URL}/chat/completions"
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}"
}

payload = {
    "model": CLAUDE_MODEL,
    "messages": [
        {
            "role": "user",
            "content": "Hello, who are you?"
        }
    ],
    "temperature": 0,
    "max_tokens": 50
}

try:
    print(f"发送 POST 请求到: {url}")
    print(f"请求头: {json.dumps({k: v if k != 'Authorization' else 'Bearer ***' for k, v in headers.items()}, indent=2)}")
    print(f"请求体: {json.dumps(payload, indent=2)}")
    print("\n正在请求...")

    response = requests.post(url, headers=headers, json=payload, timeout=30)

    print(f"\n状态码: {response.status_code}")
    print(f"响应头: {dict(response.headers)}")
    print(f"\n响应内容:")
    print(json.dumps(response.json(), indent=2, ensure_ascii=False))

    if response.status_code == 200:
        print("\n✅ 请求成功！")
    else:
        print(f"\n❌ 请求失败，状态码: {response.status_code}")

except requests.exceptions.RequestException as e:
    print(f"\n❌ 请求异常: {e}")
except Exception as e:
    print(f"\n❌ 其他错误: {e}")

print("\n" + "=" * 60)

# 方法 2: 测试 API 端点是否可达
print("\n方法 2: 测试 API 端点连通性")
print("-" * 60)

try:
    print(f"尝试访问: {LLM_BASE_URL}")
    response = requests.get(LLM_BASE_URL, timeout=10)
    print(f"状态码: {response.status_code}")
    print(f"响应: {response.text[:200]}")
except Exception as e:
    print(f"❌ 无法访问端点: {e}")

print("\n" + "=" * 60)

# 方法 3: 尝试不同的模型名称
print("\n方法 3: 尝试其他可能的模型名称")
print("-" * 60)

alternative_models = [
    "claude-opus-4-5",
    "claude-3-opus",
    "claude-3-5-sonnet-20241022",
    "claude-sonnet-3.5",
]

print("注意：这只是测试不同的模型名称是否有效")
print("备选模型列表:", alternative_models)
print("（如果主模型无法使用，可以尝试这些）")

print("\n" + "=" * 60)
print("\n💡 诊断建议：")
print("1. 检查 API Key 是否正确和有效")
print("2. 检查该 API 端点是否有 IP 白名单限制")
print("3. 确认模型名称是否正确")
print("4. 检查是否需要特殊的请求头或参数")
print("5. 联系 API 提供商确认访问权限")
print("=" * 60)

#!/usr/bin/env python3
"""
测试 Claude API 连接的简单脚本
"""

import os
import requests
import json

# API 配置
API_BASE_URL = "https://www.openclaudecode.cn/v1"
API_KEY = os.environ.get("SYNAPSERAG_OPENIE_API_KEY") or os.environ.get("SYNAPSERAG_OPENAI_API_KEY", "")
MODEL = "claude-opus-4-5-20251101"


def test_anthropic_style():
    """测试 Anthropic 原生格式 API"""
    print("=" * 50)
    print("测试 Anthropic 原生格式 (/v1/messages)")
    print("=" * 50)

    url = f"{API_BASE_URL}/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": API_KEY,
        "anthropic-version": "2023-06-01"
    }

    data = {
        "model": MODEL,
        "max_tokens": 100,
        "messages": [
            {"role": "user", "content": "你好，请用一句话介绍你自己。"}
        ]
    }

    print(f"URL: {url}")
    print(f"Model: {MODEL}")
    print()

    try:
        response = requests.post(url, headers=headers, json=data, timeout=60)
        print(f"状态码: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            print("✅ 成功!")
            print(f"回复: {result.get('content', [{}])[0].get('text', 'N/A')}")
            return True
        else:
            print(f"❌ 失败: {response.text}")
            return False
    except Exception as e:
        print(f"❌ 错误: {e}")
        return False


def test_openai_style():
    """测试 OpenAI 兼容格式 API"""
    print()
    print("=" * 50)
    print("测试 OpenAI 兼容格式 (/v1/chat/completions)")
    print("=" * 50)

    url = f"{API_BASE_URL}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}"
    }

    data = {
        "model": MODEL,
        "max_tokens": 100,
        "messages": [
            {"role": "user", "content": "你好，请用一句话介绍你自己。"}
        ]
    }

    print(f"URL: {url}")
    print(f"Model: {MODEL}")
    print()

    try:
        response = requests.post(url, headers=headers, json=data, timeout=60)
        print(f"状态码: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            print("✅ 成功!")
            content = result.get('choices', [{}])[0].get('message', {}).get('content', 'N/A')
            print(f"回复: {content}")
            return True
        else:
            print(f"❌ 失败: {response.text}")
            return False
    except Exception as e:
        print(f"❌ 错误: {e}")
        return False


if __name__ == "__main__":
    print("测试 Claude API 连接")
    print(f"Base URL: {API_BASE_URL}")
    print(f"Model: {MODEL}")
    print()

    # 测试两种格式
    anthropic_ok = test_anthropic_style()
    openai_ok = test_openai_style()

    print()
    print("=" * 50)
    print("测试结果汇总")
    print("=" * 50)
    print(f"Anthropic 格式 (/v1/messages): {'✅ 成功' if anthropic_ok else '❌ 失败'}")
    print(f"OpenAI 格式 (/v1/chat/completions): {'✅ 成功' if openai_ok else '❌ 失败'}")

    if openai_ok:
        print()
        print("✅ 可以使用 OpenAI 兼容模式，配置如下:")
        print(f"  llm_base_url: {API_BASE_URL}")
        print(f"  llm_name: {MODEL}")
    elif anthropic_ok:
        print()
        print("⚠️  只支持 Anthropic 原生格式，需要修改代码适配")

#!/usr/bin/env python3
"""
测试 Claude API 连接性
使用方法：
    conda activate synapserag
    python test_claude_connection.py
"""

import os
from openai import OpenAI

# Claude API 配置
LLM_BASE_URL = "https://www.openclaudecode.cn/v1"
API_KEY = os.environ.get("SYNAPSERAG_OPENIE_API_KEY") or os.environ.get("SYNAPSERAG_OPENAI_API_KEY", "")
CLAUDE_MODEL = "claude-opus-4-5-20251101"

def test_claude_connection():
    """测试 Claude API 连接和基本对话功能"""

    print("=" * 60)
    print("测试 Claude API 连接性")
    print("=" * 60)
    print(f"API Base URL: {LLM_BASE_URL}")
    print(f"模型: {CLAUDE_MODEL}")
    print(f"API Key: {API_KEY[:20]}...{API_KEY[-10:]}")  # 仅显示部分 Key
    print()

    try:
        # 创建 OpenAI 客户端（兼容模式）
        client = OpenAI(
            base_url=LLM_BASE_URL,
            api_key=API_KEY,
            timeout=30.0
        )

        # 发送测试请求
        print("正在发送测试请求...")
        response = client.chat.completions.create(
            model=CLAUDE_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": "请用中文简短回答：你是什么模型？"
                }
            ],
            temperature=0,
            max_tokens=100
        )

        # 输出响应
        print("\n" + "=" * 60)
        print("✅ API 连接成功！")
        print("=" * 60)
        print(f"模型响应: {response.choices[0].message.content}")
        print()
        print("响应详情:")
        print(f"  - 完成原因: {response.choices[0].finish_reason}")
        print(f"  - Prompt tokens: {response.usage.prompt_tokens}")
        print(f"  - Completion tokens: {response.usage.completion_tokens}")
        print(f"  - Total tokens: {response.usage.total_tokens}")
        print("=" * 60)

        return True

    except Exception as e:
        print("\n" + "=" * 60)
        print("❌ API 连接失败！")
        print("=" * 60)
        print(f"错误类型: {type(e).__name__}")
        print(f"错误信息: {str(e)}")
        print("=" * 60)

        return False


def test_json_mode():
    """测试 Claude API 的 JSON 模式（OpenIE 需要）"""

    print("\n" + "=" * 60)
    print("测试 JSON 响应模式（OpenIE 所需）")
    print("=" * 60)

    try:
        client = OpenAI(
            base_url=LLM_BASE_URL,
            api_key=API_KEY
        )

        print("正在测试 JSON 模式...")
        response = client.chat.completions.create(
            model=CLAUDE_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "你是一个 NER（命名实体识别）助手。请以 JSON 格式返回结果。"
                },
                {
                    "role": "user",
                    "content": '从以下文本中提取命名实体：\n"Barack Obama was born in Hawaii."\n\n请返回 JSON 格式：{"named_entities": ["实体1", "实体2"]}'
                }
            ],
            temperature=0,
            max_tokens=200,
            response_format={"type": "json_object"}
        )

        print("\n✅ JSON 模式测试成功！")
        print(f"模型响应: {response.choices[0].message.content}")
        print("=" * 60)

        return True

    except Exception as e:
        print(f"\n⚠️  JSON 模式测试失败: {e}")
        print("注意：如果不支持 JSON 模式，OpenIE 可能需要解析普通文本响应")
        print("=" * 60)

        return False


if __name__ == "__main__":
    # 测试基本连接
    basic_ok = test_claude_connection()

    if basic_ok:
        # 测试 JSON 模式
        json_ok = test_json_mode()

        print("\n" + "=" * 60)
        print("测试总结")
        print("=" * 60)
        print(f"✅ 基本连接: {'通过' if basic_ok else '失败'}")
        print(f"{'✅' if json_ok else '⚠️ '} JSON 模式: {'通过' if json_ok else '失败（可能需要调整）'}")
        print("=" * 60)

        if basic_ok and json_ok:
            print("\n🎉 Claude API 完全可用！可以继续进行 OpenIE 脚本开发。")
        elif basic_ok:
            print("\n✅ Claude API 基本可用，但 JSON 模式可能需要调整。")
    else:
        print("\n❌ 请检查 API 配置后重试。")

"""复用 TIA 的 OpenAI 兼容小模型客户端。"""

from __future__ import annotations

from tactical_intelligence_agent.llm.client import (
    LLMClientError,
    OpenAICompatibleClient,
    ToolLLMSettings,
)

__all__ = ["LLMClientError", "OpenAICompatibleClient", "ToolLLMSettings"]

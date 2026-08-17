"""Knowledge Semantic Communication 语义压缩。"""

from __future__ import annotations

from typing import Any

from agent.inference.registry import get_semantic_comm


def compress_intelligence(
    perception: dict[str, Any],
    cognition: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    model = get_semantic_comm(config)
    return model.compress(perception, cognition)

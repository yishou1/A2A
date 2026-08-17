"""ImageBind 跨模态嵌入。"""

from __future__ import annotations

from typing import Any

from agent.inference.registry import get_imagebind


def embed_frames(frames: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, list[float]]:
    embedder = get_imagebind(config)
    return embedder.embed_frames(frames)

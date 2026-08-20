"""Multimodal Mamba 多模态融合。"""

from __future__ import annotations

from typing import Any

from agent.inference.registry import get_device, get_mamba_fusion


def fuse_embeddings(
    embeddings: dict[str, list[float]],
    tracks: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    if not embeddings:
        dim = int(config.get("embed_dim", 512))
        return {"fused_embeddings": {}, "sequence_length": len(tracks)}

    model = get_mamba_fusion(config)
    device = get_device(config)
    fused = model.fuse(embeddings, tracks, device=device)
    return {"fused_embeddings": fused, "sequence_length": max(len(tracks), len(embeddings), 1)}

"""Multimodal Mamba：多模态状态空间融合。

主路径为确定性均值池化融合（稳定可行）；神经网络 SSM 为可选增强。
"""

from __future__ import annotations

from typing import Any

from algorithms.base import AlgorithmBackend


class MultimodalMambaFusion(AlgorithmBackend[dict[str, Any]]):
    name = "Multimodal-Mamba"
    algorithm_id = "multimodal_mamba_fusion"
    config_key = "multimodal_mamba"

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        embeddings = inputs.get("embeddings", {})
        tracks = inputs.get("tracks", [])
        # 默认优先确定性融合；仅当显式 use_neural=true 且非 mock 时走神经网络
        prefer_deterministic = bool(self.config.get("prefer_deterministic", True))
        if self.use_mock or prefer_deterministic:
            return self._deterministic_fuse(embeddings, tracks)
        return self._infer(embeddings, tracks)

    def _deterministic_fuse(
        self, embeddings: dict[str, list[float]], tracks: list[dict[str, Any]]
    ) -> dict[str, Any]:
        fused: dict[str, list[float]] = {}
        for track in tracks:
            tid = track.get("track_id", "unknown")
            parts = list(embeddings.values())
            if parts:
                dim = len(parts[0])
                fused[tid] = [sum(p[i] for p in parts) / len(parts) for i in range(dim)]
            else:
                fused[tid] = [0.0] * 64
        return {
            "fused_embeddings": fused,
            "sequence_length": len(tracks),
            "fusion_mode": "deterministic_mean_pool",
        }

    def _infer(
        self, embeddings: dict[str, list[float]], tracks: list[dict[str, Any]]
    ) -> dict[str, Any]:
        from agent.inference.fusion import fuse_embeddings

        result = fuse_embeddings(embeddings, tracks, self.config)
        if isinstance(result, dict):
            result.setdefault("fusion_mode", "neural_mamba")
        return result

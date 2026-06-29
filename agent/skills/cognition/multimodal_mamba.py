"""Multimodal Mamba：多模态状态空间融合。"""

from __future__ import annotations

from typing import Any

from agent.skills.base import AlgorithmBackend


class MultimodalMambaFusion(AlgorithmBackend[dict[str, Any]]):
    name = "Multimodal-Mamba"

    def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        embeddings = inputs.get("embeddings", {})
        tracks = inputs.get("tracks", [])
        if self.use_mock:
            return self._mock_fuse(embeddings, tracks)
        return self._infer(embeddings, tracks)

    def _mock_fuse(
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
        return {"fused_embeddings": fused, "sequence_length": len(tracks)}

    def _infer(
        self, embeddings: dict[str, list[float]], tracks: list[dict[str, Any]]
    ) -> dict[str, Any]:
        from agent.inference.fusion import fuse_embeddings

        return fuse_embeddings(embeddings, tracks, self.config)

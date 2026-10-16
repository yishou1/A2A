"""多模态嵌入：low→MobileNet；mid→ResNet18；high→ImageBind/CLIP（registry 按档）。"""

from __future__ import annotations

import hashlib
from typing import Any

from algorithms.base import AlgorithmBackend


class ImageBindEncoder(AlgorithmBackend[dict[str, list[float]]]):
    name = "ImageBind-CrossModal"
    algorithm_id = "imagebind_multimodal_encoder"
    config_key = "imagebind"

    def run(self, inputs: dict[str, Any]) -> dict[str, list[float]]:
        frames = inputs.get("frames", [])
        if self.use_mock:
            return self._mock_embed(frames)
        return self._infer(frames)

    def _embed_dim(self) -> int:
        return int(self.config.get("embed_dim", 1024))

    def _mock_embed(self, frames: list[dict[str, Any]]) -> dict[str, list[float]]:
        dim = self._embed_dim()
        embeddings: dict[str, list[float]] = {}
        for frame in frames:
            sid = frame.get("sensor_id", "unknown")
            seed = hashlib.sha256(str(frame).encode()).digest()
            vec = [(seed[i % len(seed)] / 255.0) for i in range(dim)]
            embeddings[sid] = vec
        return embeddings

    def _infer(self, frames: list[dict[str, Any]]) -> dict[str, list[float]]:
        from agent.inference.embed import embed_frames

        return embed_frames(frames, self.config)

"""ImageBind：跨模态统一表征。"""

from __future__ import annotations

import hashlib
from typing import Any

from agent.skills.base import AlgorithmBackend


class ImageBindEncoder(AlgorithmBackend[dict[str, list[float]]]):
    name = "ImageBind-CrossModal"

    EMBED_DIM = 64

    def run(self, inputs: dict[str, Any]) -> dict[str, list[float]]:
        frames = inputs.get("frames", [])
        if self.use_mock:
            return self._mock_embed(frames)
        return self._infer(frames)

    def _mock_embed(self, frames: list[dict[str, Any]]) -> dict[str, list[float]]:
        embeddings: dict[str, list[float]] = {}
        for frame in frames:
            sid = frame.get("sensor_id", "unknown")
            seed = hashlib.sha256(str(frame).encode()).digest()
            vec = [(seed[i % len(seed)] / 255.0) for i in range(self.EMBED_DIM)]
            embeddings[sid] = vec
        return embeddings

    def _infer(self, frames: list[dict[str, Any]]) -> dict[str, list[float]]:
        from agent.inference.embed import embed_frames

        return embed_frames(frames, self.config)

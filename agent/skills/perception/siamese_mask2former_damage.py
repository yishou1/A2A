"""Siamese Mask2Former：孪生掩码战损/毁伤检测。"""

from __future__ import annotations

from typing import Any

from agent.skills.base import AlgorithmBackend


class SiameseMask2FormerDamage(AlgorithmBackend[list[dict[str, Any]]]):
    name = "Siamese-Mask2Former"

    def run(self, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        frames = inputs.get("frames", [])
        reference = inputs.get("reference_frame")
        if self.use_mock:
            return self._mock_damage(frames, reference)
        return self._infer(frames, reference)

    def _mock_damage(
        self, frames: list[dict[str, Any]], reference: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        if not reference or not frames:
            return []
        return [
            {
                "sensor_id": frames[0].get("sensor_id"),
                "damage_score": 0.74,
                "change_ratio": 0.19,
                "damage_mask_ref": "siamese_mask2former_mock",
            }
        ]

    def _infer(
        self, frames: list[dict[str, Any]], reference: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        from agent.inference.damage import assess_damage

        return assess_damage(frames, reference, self.config)

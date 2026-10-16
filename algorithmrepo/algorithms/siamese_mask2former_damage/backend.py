"""孪生毁伤评估：low→轻量 CNN；mid/high→Mask2Former（由 registry 按档选择）。"""

from __future__ import annotations

from typing import Any

from algorithms.base import AlgorithmBackend


class SiameseMask2FormerDamage(AlgorithmBackend[list[dict[str, Any]]]):
    name = "Siamese-Mask2Former"
    algorithm_id = "siamese_mask2former_damage"
    config_key = "siamese_mask2former"

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
        from algorithms.compute_tiers import normalize_param_tier, resolve_tier_name

        backend = str(self.config.get("damage_backend", "auto")).lower()
        tier = resolve_tier_name(config=self.config, env=True)
        tier = normalize_param_tier(tier)
        use_light = backend in {"lightweight", "light", "tiny"} or (
            backend in {"auto", ""} and tier == "low"
        )
        mask_ref = "lightweight_siamese" if use_light else "siamese_mask2former"
        change_ratio = 0.19
        return [
            {
                "sensor_id": frames[0].get("sensor_id"),
                "damage_score": 0.74,
                "change_ratio": change_ratio,
                "damage_mask_ref": mask_ref,
                "mask_pixels": int(change_ratio * 64 * 64) if use_light else 4096,
            }
        ]

    def _infer(
        self, frames: list[dict[str, Any]], reference: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        from agent.inference.damage import assess_damage

        return assess_damage(frames, reference, self.config)

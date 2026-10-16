"""siamese_mask2former_damage — 独立算法入口。"""

from __future__ import annotations

from typing import Any

from algorithms.siamese_mask2former_damage.backend import SiameseMask2FormerDamage

ALGORITHM_ID = "siamese_mask2former_damage"
CONFIG_KEY = "siamese_mask2former"


def predict(
    inputs: dict[str, Any],
    *,
    use_mock: bool = True,
    config: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = dict(config or {})
    if params:
        cfg.update(params)
    reports = SiameseMask2FormerDamage(use_mock=use_mock, config=cfg).run(
        {
            "frames": inputs.get("frames") or [],
            "reference_frame": inputs.get("reference_frame"),
        }
    )
    return {"damage_reports": reports, "count": len(reports)}


__all__ = ["ALGORITHM_ID", "CONFIG_KEY", "SiameseMask2FormerDamage", "predict"]

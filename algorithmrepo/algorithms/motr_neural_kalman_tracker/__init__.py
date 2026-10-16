"""motr_neural_kalman_tracker — 独立算法入口。"""

from __future__ import annotations

from typing import Any

from algorithms.motr_neural_kalman_tracker.backend import MOTRNeuralKalmanTracker

ALGORITHM_ID = "motr_neural_kalman_tracker"
CONFIG_KEY = "motr_neural_kalman"


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
    return MOTRNeuralKalmanTracker(use_mock=use_mock, config=cfg).run(
        {
            "verified_detections": inputs.get("verified_detections") or [],
            "prior_tracks": inputs.get("prior_tracks") or [],
            "visual_frame": inputs.get("visual_frame"),
            "batch_context": inputs.get("batch_context") or {},
        }
    )


__all__ = ["ALGORITHM_ID", "CONFIG_KEY", "MOTRNeuralKalmanTracker", "predict"]

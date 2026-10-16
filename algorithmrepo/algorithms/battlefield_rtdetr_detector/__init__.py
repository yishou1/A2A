"""battlefield_rtdetr_detector — 独立算法入口。"""

from __future__ import annotations

from typing import Any

from algorithms.battlefield_rtdetr_detector.backend import RTDETRODConvDetector

ALGORITHM_ID = "battlefield_rtdetr_detector"
CONFIG_KEY = "rt_detr_odconv"


def predict(
    inputs: dict[str, Any],
    *,
    use_mock: bool = True,
    config: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """独立推理。inputs 需含 frames（或 media_refs）。"""
    cfg = dict(config or {})
    if params:
        cfg.update(params)
    frames = list(inputs.get("frames") or [])
    if not frames and inputs.get("media_refs"):
        frames = [
            {
                "sensor_id": "MEDIA-0",
                "modality": "eo_ir",
                "payload": {"media_refs": inputs["media_refs"]},
            }
        ]
    detections = RTDETRODConvDetector(use_mock=use_mock, config=cfg).run({"frames": frames})
    return {"detections": detections, "count": len(detections)}


__all__ = ["ALGORITHM_ID", "CONFIG_KEY", "RTDETRODConvDetector", "predict"]

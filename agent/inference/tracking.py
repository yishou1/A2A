"""MOTR + Neural Kalman 多目标跟踪。"""

from __future__ import annotations

from typing import Any

from agent.inference.registry import get_device, get_motr_tracker
from agent.inference.utils import decode_image_from_frame


def track_objects(
    verified: list[dict[str, Any]],
    prior_tracks: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    visual_frame: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tracker = get_motr_tracker(config)
    device = get_device(config)
    iou_thr = float(config.get("motr_iou_threshold", 0.3))
    base_lat = float(config.get("base_lat", 30.512))
    base_lon = float(config.get("base_lon", 114.381))

    image_rgb = None
    if visual_frame is not None:
        image_rgb = decode_image_from_frame(visual_frame)

    return tracker.track(
        verified,
        prior_tracks,
        image_rgb,
        device=device,
        iou_thr=iou_thr,
        base_lat=base_lat,
        base_lon=base_lon,
    )

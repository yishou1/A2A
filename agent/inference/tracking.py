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
    batch_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tracker = get_motr_tracker(config)
    device = get_device(config)
    iou_thr = float(config.get("motr_iou_threshold", 0.3))

    image_rgb = None
    image_width: int | None = None
    image_height: int | None = None
    if visual_frame is not None:
        image_rgb = decode_image_from_frame(visual_frame)
        if image_rgb is not None:
            image_height, image_width = image_rgb.shape[:2]

    return tracker.track(
        verified,
        prior_tracks,
        image_rgb,
        device=device,
        iou_thr=iou_thr,
        config=config,
        visual_frame=visual_frame,
        batch_context=batch_context,
        image_width=image_width,
        image_height=image_height,
    )

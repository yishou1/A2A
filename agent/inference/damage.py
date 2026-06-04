"""Siamese Mask2Former 毁伤检测。"""

from __future__ import annotations

from typing import Any

from agent.inference.registry import get_siamese_mask2former
from agent.inference.utils import decode_image_from_frame


def assess_damage(
    frames: list[dict[str, Any]],
    reference: dict[str, Any] | None,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    if not reference or not frames:
        return []

    ref_img = decode_image_from_frame(reference)
    if ref_img is None:
        return []

    model = get_siamese_mask2former(config)
    gain = float(config.get("mask2former_gain", 4.0))
    reports: list[dict[str, Any]] = []

    for frame in frames:
        if frame.get("modality") not in ("eo_ir", "sar"):
            continue
        cur = decode_image_from_frame(frame)
        if cur is None:
            continue
        reports.append(
            model.assess_pair(
                ref_img,
                cur,
                sensor_id=frame.get("sensor_id", "unknown"),
                gain=gain,
            )
        )
    return reports

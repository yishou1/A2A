"""EDL 证据深度学习检测验证。"""

from __future__ import annotations

from typing import Any

from agent.inference.registry import get_device, get_edl_head


def verify_detections(
    candidates: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    head = get_edl_head(config)
    device = get_device(config)
    min_conf = float(config.get("edl_min_confidence", 0.35))
    max_epistemic = float(config.get("edl_max_epistemic", 0.45))
    return head.verify(
        candidates,
        device=device,
        min_conf=min_conf,
        max_epistemic=max_epistemic,
    )

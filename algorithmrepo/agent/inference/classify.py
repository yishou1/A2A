"""SupCon + Meta-Learning 目标分类。"""

from __future__ import annotations

from typing import Any

from agent.inference.registry import get_device, get_supcon_meta


def classify_targets(
    fused: dict[str, list[float]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    model = get_supcon_meta(config)
    device = get_device(config)
    temperature = float(config.get("supcon_temperature", 0.07))
    return model.classify(
        fused,
        device=device,
        support_shots=config.get("support_shots") or [],
        temperature=temperature,
    )

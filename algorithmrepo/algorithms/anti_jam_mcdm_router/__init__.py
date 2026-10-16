"""anti_jam_mcdm_router — 独立算法入口。

生产默认：SAW 多属性抗干扰路由（可审计）。
可选：PPO 信道策略（scripts/train_anti_jam_mcdm_router.py）。
"""

from __future__ import annotations

from typing import Any

from algorithms.anti_jam_mcdm_router.backend import AntiJamMCDMRouter
from algorithms.anti_jam_mcdm_router.mcdm import PROVENANCE, mcdm_route

ALGORITHM_ID = "anti_jam_mcdm_router"
CONFIG_KEY = "anti_jam_mcdm_router"
DISPLAY_NAME = "Anti-Jam MCDM Router (SAW + optional PPO)"


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
    return AntiJamMCDMRouter(use_mock=use_mock, config=cfg).run(
        {
            "packet": inputs.get("packet") or {},
            "subscriber_agents": inputs.get("subscriber_agents") or [],
            "jamming_level": float(inputs.get("jamming_level", 0.0)),
        }
    )


__all__ = [
    "ALGORITHM_ID",
    "CONFIG_KEY",
    "DISPLAY_NAME",
    "AntiJamMCDMRouter",
    "PROVENANCE",
    "mcdm_route",
    "predict",
]

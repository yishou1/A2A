"""multimodal_mamba_fusion — 独立算法入口。"""

from __future__ import annotations

from typing import Any

from algorithms.multimodal_mamba_fusion.backend import MultimodalMambaFusion

ALGORITHM_ID = "multimodal_mamba_fusion"
CONFIG_KEY = "multimodal_mamba"


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
    return MultimodalMambaFusion(use_mock=use_mock, config=cfg).run(
        {
            "embeddings": inputs.get("embeddings") or {},
            "tracks": inputs.get("tracks") or [],
        }
    )


__all__ = ["ALGORITHM_ID", "CONFIG_KEY", "MultimodalMambaFusion", "predict"]

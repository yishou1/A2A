"""supcon_meta_classifier — 独立算法入口。"""

from __future__ import annotations

from typing import Any

from algorithms.supcon_meta_classifier.backend import SupConMetaClassifier

ALGORITHM_ID = "supcon_meta_classifier"
CONFIG_KEY = "supcon_meta"


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
    classifications = SupConMetaClassifier(use_mock=use_mock, config=cfg).run(
        {
            "fused_embeddings": inputs.get("fused_embeddings") or {},
            "support_shots": inputs.get("support_shots") or [],
        }
    )
    return {"classifications": classifications, "count": len(classifications)}


__all__ = ["ALGORITHM_ID", "CONFIG_KEY", "SupConMetaClassifier", "predict"]

"""imagebind_multimodal_encoder — 独立算法入口。"""

from __future__ import annotations

from typing import Any

from algorithms.imagebind_multimodal_encoder.backend import ImageBindEncoder

ALGORITHM_ID = "imagebind_multimodal_encoder"
CONFIG_KEY = "imagebind"


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
    embeddings = ImageBindEncoder(use_mock=use_mock, config=cfg).run(
        {"frames": inputs.get("frames") or []}
    )
    # 与 algorithms.compute_tiers 档位语义对齐（mock 也回传；不依赖 agent）
    from algorithms.compute_tiers import resolve_tier_name

    tier = resolve_tier_name(config=cfg, env=True)
    backend = str(cfg.get("embed_backend", "auto")).lower()
    if backend in {"auto", ""}:
        backend = {
            "low": "lightweight_low",
            "mid": "lightweight_mid",
            "high": "imagebind",
        }.get(tier, "imagebind")
    dim = 0
    if embeddings:
        first = next(iter(embeddings.values()))
        dim = len(first) if isinstance(first, list) else int(cfg.get("embed_dim", 1024))
    return {
        "embeddings": embeddings,
        "count": len(embeddings),
        "embed_backend": backend,
        "embed_dim": dim or int(cfg.get("embed_dim", 1024)),
        "param_tier": tier,
    }


__all__ = ["ALGORITHM_ID", "CONFIG_KEY", "ImageBindEncoder", "predict"]

"""按 work_item / URI 缓存已解码图像，避免同一 URL 在感知/认知中重复 GET。"""

from __future__ import annotations

from typing import Any

import numpy as np

_CACHE: dict[str, np.ndarray] = {}
_ACTIVE_SCOPE: str | None = None


def begin_batch_cache(scope: str) -> None:
    global _ACTIVE_SCOPE
    _ACTIVE_SCOPE = scope
    _CACHE.clear()


def end_batch_cache() -> None:
    global _ACTIVE_SCOPE
    _ACTIVE_SCOPE = None
    _CACHE.clear()


def cache_get(uri: str) -> np.ndarray | None:
    if not uri:
        return None
    return _CACHE.get(uri)


def cache_put(uri: str, rgb: np.ndarray) -> None:
    if uri and _ACTIVE_SCOPE:
        _CACHE[uri] = rgb


def prefetch_visual_frames(frames: list[dict[str, Any]]) -> int:
    """预拉取视觉帧图像，返回成功缓存的数量。"""
    from agent.inference.utils import decode_image_from_frame
    from attachment_fetcher import resolve_image_uri_from_frame

    loaded = 0
    for frame in frames:
        if frame.get("modality") not in ("eo_ir", "sar"):
            continue
        uri = resolve_image_uri_from_frame(frame)
        if uri and cache_get(uri) is not None:
            loaded += 1
            continue
        rgb = decode_image_from_frame(frame, use_cache=False)
        if rgb is not None and uri:
            cache_put(uri, rgb)
            loaded += 1
    return loaded

"""请求隔离的批级图像缓存，避免并发任务互相清空缓存。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

import numpy as np


@dataclass
class BatchImageCache:
    """单次推理批次的 URI -> RGB 缓存，绝不跨请求共享。"""

    scope: str
    images: dict[str, np.ndarray] = field(default_factory=dict)


_ACTIVE_CACHE: ContextVar[Optional[BatchImageCache]] = ContextVar(
    "tia_active_image_cache", default=None
)


def begin_batch_cache(scope: str) -> Token[Optional[BatchImageCache]]:
    """为当前上下文创建独立缓存，并返回用于恢复父上下文的 token。"""
    return _ACTIVE_CACHE.set(BatchImageCache(scope=str(scope)))


def end_batch_cache(token: Optional[Token[Optional[BatchImageCache]]] = None) -> None:
    """结束当前批次缓存；传入 token 时精确恢复嵌套调用的父上下文。"""
    if token is not None:
        _ACTIVE_CACHE.reset(token)
    else:
        _ACTIVE_CACHE.set(None)


@contextmanager
def batch_cache_scope(scope: str) -> Iterator[BatchImageCache]:
    token = begin_batch_cache(scope)
    cache = _ACTIVE_CACHE.get()
    assert cache is not None
    try:
        yield cache
    finally:
        end_batch_cache(token)


def active_scope() -> Optional[str]:
    cache = _ACTIVE_CACHE.get()
    return cache.scope if cache is not None else None


def cache_get(uri: str) -> Optional[np.ndarray]:
    if not uri:
        return None
    cache = _ACTIVE_CACHE.get()
    return cache.images.get(uri) if cache is not None else None


def cache_put(uri: str, rgb: np.ndarray) -> None:
    if not uri:
        return
    cache = _ACTIVE_CACHE.get()
    if cache is not None:
        cache.images[uri] = rgb


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

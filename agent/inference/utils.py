"""帧解码、设备选择与通用工具。"""

from __future__ import annotations

import base64
import io
import json
from typing import Any

import numpy as np

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None  # type: ignore


def resolve_device(config: dict[str, Any] | None = None) -> str:
    cfg = config or {}
    want = str(cfg.get("device", "auto")).lower()
    if want in ("cpu", "cuda", "mps"):
        return want
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def get_device(config: dict[str, Any] | None = None) -> str:
    return resolve_device(config)


def decode_image_from_frame(frame: dict[str, Any]) -> np.ndarray | None:
    """从帧 payload 解码为 RGB uint8 数组 (H,W,3)。"""
    if Image is None:
        raise ImportError("真实推理需要 Pillow: pip install Pillow")

    payload = frame.get("payload") or {}
    b64 = payload.get("image_base64")
    if not b64:
        return None
    try:
        raw = base64.b64decode(b64, validate=False)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        return np.array(img)
    except Exception:
        return None


def frame_to_text(frame: dict[str, Any]) -> str:
    """非图像模态：序列化为文本供 CLIP / RAG 使用。"""
    modality = frame.get("modality", "unknown")
    payload = frame.get("payload") or {}
    if modality == "text_report" and "report_text" in payload:
        return str(payload["report_text"])
    return f"[{modality}] {json.dumps(payload, ensure_ascii=False)[:800]}"


def bbox_iou(a: list[float], b: list[float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_x1, inter_y1 = max(ax1, bx1), max(ay1, by1)
    inter_x2, inter_y2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, inter_x2 - inter_x1), max(0.0, inter_y2 - inter_y1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, (ax2 - ax1) * (ay2 - ay1))
    area_b = max(0.0, (bx2 - bx1) * (by2 - by1))
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0

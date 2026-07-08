"""在图像上绘制检测框（供产物上传 / 可视化）。"""

from __future__ import annotations

from typing import Any

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore

_CLASS_COLORS: dict[str, tuple[int, int, int]] = {
    "plane": (255, 128, 0),
    "ship": (0, 200, 255),
    "storage-tank": (200, 200, 0),
    "large-vehicle": (0, 255, 255),
    "small-vehicle": (0, 80, 255),
    "helicopter": (255, 0, 200),
}


def _color_for_class(class_name: str) -> tuple[int, int, int]:
    return _CLASS_COLORS.get(class_name, (0, 255, 0))


def draw_detections_on_image(
    rgb: np.ndarray,
    detections: list[dict[str, Any]],
) -> np.ndarray:
    """在 RGB 数组上绘制 bbox + 类别/置信度标签，返回 RGB 数组。"""
    if cv2 is None:
        raise ImportError("draw_detections_on_image requires opencv-python")

    canvas = rgb.copy()
    for det in detections:
        bbox = det.get("bbox")
        if not bbox or len(bbox) < 4:
            continue
        x1, y1, x2, y2 = (int(round(v)) for v in bbox[:4])
        class_name = str(det.get("class_name", "unknown"))
        conf = float(det.get("confidence", 0.0))
        color = _color_for_class(class_name)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
        label = f"{class_name} {conf:.2f}"
        (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        ty = max(y1 - 4, th + 4)
        cv2.rectangle(canvas, (x1, ty - th - baseline - 2), (x1 + tw + 4, ty + 2), color, -1)
        cv2.putText(
            canvas,
            label,
            (x1 + 2, ty - 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 0, 0),
            1,
            cv2.LINE_AA,
        )
    return canvas


def encode_jpeg_bytes(rgb: np.ndarray, *, quality: int = 90) -> bytes:
    if cv2 is None:
        raise ImportError("encode_jpeg_bytes requires opencv-python")
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("failed to encode annotated JPEG")
    return buf.tobytes()

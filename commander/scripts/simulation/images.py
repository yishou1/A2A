"""
为真实/模拟推理准备视觉场景图。

优先使用 Ultralytics 自带的 bus.jpg（YOLOv8 可稳定检出车辆）；
否则下载到本地缓存；最后才回退到程序绘制的合成场景。
"""

from __future__ import annotations

import base64
import io
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance

_CACHE_DIR = Path(__file__).resolve().parent / "_cache"
_BUS_CACHE = _CACHE_DIR / "bus.jpg"
_BUS_URL = "https://ultralytics.com/images/bus.jpg"


def _ultralytics_asset(name: str) -> Path | None:
    try:
        import ultralytics
    except ImportError:
        return None
    path = Path(ultralytics.__file__).resolve().parent / "assets" / name
    return path if path.is_file() else None


def _download_bus() -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not _BUS_CACHE.is_file():
        req = urllib.request.Request(
            _BUS_URL,
            headers={"User-Agent": "TacticalIntelligenceAgent/1.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            _BUS_CACHE.write_bytes(resp.read())
    return _BUS_CACHE


def _synthetic_vehicle_scene(size: tuple[int, int] = (640, 480)) -> np.ndarray:
    """无权重时的兜底场景（不保证 YOLO 有检出）。"""
    w, h = size
    img = Image.new("RGB", (w, h), (40, 55, 35))
    draw = ImageDraw.Draw(img)
    draw.rectangle((80, 180, 560, 380), fill=(90, 90, 95), outline=(30, 30, 30), width=3)
    draw.rectangle((120, 200, 220, 320), fill=(60, 80, 120))
    draw.rectangle((380, 200, 520, 320), fill=(60, 80, 120))
    draw.ellipse((140, 340, 200, 400), fill=(20, 20, 20))
    draw.ellipse((420, 340, 480, 400), fill=(20, 20, 20))
    return np.array(img)


def load_base_scene_rgb() -> np.ndarray:
    asset = _ultralytics_asset("bus.jpg")
    if asset:
        return np.array(Image.open(asset).convert("RGB"))
    try:
        return np.array(Image.open(_download_bus()).convert("RGB"))
    except Exception:
        return _synthetic_vehicle_scene()


def make_damaged_scene_rgb(base: np.ndarray, *, severity: float = 0.4) -> np.ndarray:
    """在基准场景上叠加毁伤区域，供 OpenCV 帧差与 BDA 阶段使用。"""
    img = Image.fromarray(base.copy())
    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    cx, cy = int(w * 0.55), int(h * 0.52)
    rw, rh = int(w * 0.22), int(h * 0.18)
    alpha = int(120 + 100 * min(1.0, max(0.0, severity)))
    draw.ellipse(
        (cx - rw, cy - rh, cx + rw, cy + rh),
        fill=(200, 80, 30, alpha),
    )
    draw.rectangle(
        (cx - rw // 2, cy - rh // 2, cx + rw // 2, cy + rh // 2),
        fill=(40, 40, 40, alpha // 2),
    )
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    img = ImageEnhance.Brightness(img).enhance(0.88 - 0.12 * severity)
    return np.array(img)


def encode_image_b64(rgb: np.ndarray, *, quality: int = 85) -> str:
    pil = Image.fromarray(rgb)
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def resize_rgb(rgb: np.ndarray, max_side: int = 640) -> np.ndarray:
    pil = Image.fromarray(rgb)
    w, h = pil.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        pil = pil.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    return np.array(pil)

"""xBD handcrafted ROI features and model vector assembly."""
from __future__ import annotations

import math
import re
from typing import Any, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

_POINT_RE = re.compile(r"[-+]?(?:\d+\.\d+|\d+)")


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def parse_polygon_wkt(wkt: str) -> List[Tuple[float, float]]:
    text = (wkt or "").strip()
    if not text.upper().startswith("POLYGON"):
        return []
    start = text.find("((")
    end = text.find("))", start + 2)
    if start < 0 or end < 0:
        return []
    exterior = text[start + 2 : end].split("),", 1)[0]
    numbers = [float(item) for item in _POINT_RE.findall(exterior)]
    if len(numbers) < 6:
        return []
    return [(numbers[i], numbers[i + 1]) for i in range(0, len(numbers) - 1, 2)]


def normalize_polygon(polygon: Any) -> Optional[List[Tuple[float, float]]]:
    if polygon is None:
        return None
    if isinstance(polygon, str):
        parsed = parse_polygon_wkt(polygon)
        return parsed if len(parsed) >= 3 else None
    if isinstance(polygon, Sequence):
        points: List[Tuple[float, float]] = []
        for item in polygon:
            if isinstance(item, Sequence) and len(item) >= 2:
                points.append((float(item[0]), float(item[1])))
        return points if len(points) >= 3 else None
    return None


def bbox(points: List[Tuple[float, float]], width: int, height: int) -> Optional[Tuple[int, int, int, int]]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    if not xs or not ys:
        return None
    left = max(0, int(math.floor(min(xs))))
    top = max(0, int(math.floor(min(ys))))
    right = min(width - 1, int(math.ceil(max(xs))))
    bottom = min(height - 1, int(math.ceil(max(ys))))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _gradient(gray: List[List[float]], x: int, y: int) -> float:
    height = len(gray)
    width = len(gray[0]) if height else 0
    left = gray[y][max(0, x - 1)]
    right = gray[y][min(width - 1, x + 1)]
    up = gray[max(0, y - 1)][x]
    down = gray[min(height - 1, y + 1)][x]
    return abs(right - left) + abs(down - up)


def polygon_features(
    pre_image: Image.Image,
    post_image: Image.Image,
    polygon: List[Tuple[float, float]],
) -> Optional[dict]:
    width, height = pre_image.size
    box = bbox(polygon, width, height)
    if box is None:
        return None
    left, top, right, bottom = box
    crop_box = (left, top, right + 1, bottom + 1)
    pre_crop = pre_image.crop(crop_box).convert("RGB")
    post_crop = post_image.crop(crop_box).convert("RGB")
    mask = Image.new("L", pre_crop.size, 0)
    shifted = [(x - left, y - top) for x, y in polygon]
    ImageDraw.Draw(mask).polygon(shifted, fill=255)

    pre_pixels = pre_crop.load()
    post_pixels = post_crop.load()
    mask_pixels = mask.load()
    crop_w, crop_h = pre_crop.size

    pre_gray: List[List[float]] = [[0.0 for _ in range(crop_w)] for _ in range(crop_h)]
    post_gray: List[List[float]] = [[0.0 for _ in range(crop_w)] for _ in range(crop_h)]
    for y in range(crop_h):
        for x in range(crop_w):
            pr, pg, pb = pre_pixels[x, y]
            qr, qg, qb = post_pixels[x, y]
            pre_gray[y][x] = (pr + pg + pb) / (3.0 * 255.0)
            post_gray[y][x] = (qr + qg + qb) / (3.0 * 255.0)

    area = 0
    spectral_sum = 0.0
    brightness_sum = 0.0
    texture_sum = 0.0
    dark_or_changed = 0
    high_change = 0
    severe_damage = 0
    collapse = 0
    pre_brightness_sum = 0.0
    post_brightness_sum = 0.0
    spectral_values: List[float] = []
    max_spectral = 0.0
    cx_sum = 0.0
    cy_sum = 0.0
    for y in range(crop_h):
        for x in range(crop_w):
            if mask_pixels[x, y] <= 0:
                continue
            area += 1
            cx_sum += left + x
            cy_sum += top + y
            pr, pg, pb = pre_pixels[x, y]
            qr, qg, qb = post_pixels[x, y]
            pre_brightness = pre_gray[y][x]
            post_brightness = post_gray[y][x]
            spectral = (abs(qr - pr) + abs(qg - pg) + abs(qb - pb)) / (3.0 * 255.0)
            spectral_sum += spectral
            spectral_values.append(spectral)
            max_spectral = max(max_spectral, spectral)
            pre_brightness_sum += pre_brightness
            post_brightness_sum += post_brightness
            brightness_delta = abs(post_brightness - pre_brightness)
            brightness_sum += brightness_delta
            texture_delta = abs(_gradient(post_gray, x, y) - _gradient(pre_gray, x, y)) / 2.0
            texture_sum += texture_delta
            if spectral > 0.18 or post_brightness < pre_brightness - 0.12:
                dark_or_changed += 1
            if spectral > 0.15:
                high_change += 1
            if spectral > 0.20 and post_brightness < pre_brightness - 0.06:
                severe_damage += 1
            if post_brightness < pre_brightness - 0.14:
                collapse += 1

    if area <= 0:
        return None
    cx = cx_sum / area
    cy = cy_sum / area
    center_dist = math.sqrt((cx - width / 2.0) ** 2 + (cy - height / 2.0) ** 2)
    max_dist = math.sqrt((width / 2.0) ** 2 + (height / 2.0) ** 2) or 1.0
    mean_spectral = spectral_sum / area
    spectral_variance = sum((value - mean_spectral) ** 2 for value in spectral_values) / area
    pre_brightness_mean = pre_brightness_sum / area
    post_brightness_mean = post_brightness_sum / area
    return {
        "pre_area": clamp(area / float(width * height)),
        "spectral_delta": clamp(mean_spectral),
        "texture_delta": clamp(texture_sum / area),
        "heat_signature": clamp(brightness_sum / area),
        "crater_density": clamp(dark_or_changed / float(area)),
        "std_spectral": clamp(math.sqrt(spectral_variance)),
        "max_spectral": clamp(max_spectral),
        "high_change_ratio": clamp(high_change / float(area)),
        "severe_damage_ratio": clamp(severe_damage / float(area)),
        "collapse_ratio": clamp(collapse / float(area)),
        "post_brightness": clamp(post_brightness_mean),
        "brightness_drop": clamp(max(0.0, pre_brightness_mean - post_brightness_mean)),
        "normalized_distance": clamp(center_dist / max_dist),
        "detection_confidence": 1.0,
        "threat_score": 0.5,
    }


def as_float(row: dict, names: Sequence[str], default: float = 0.0) -> float:
    for name in names:
        value = row.get(name)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return default


def disaster_name(sample_id: Any) -> str:
    text = str(sample_id or "").strip()
    if not text:
        return "unknown"
    if "-" in text and text.split("-")[-1].isdigit():
        return text.rsplit("_", 1)[0]
    return text.split("_")[0] if "_" in text else text


def disaster_bucket_features(sample_id: Any, buckets: int = 10) -> List[float]:
    slot = abs(hash(disaster_name(sample_id))) % max(1, buckets)
    return [1.0 if idx == slot else 0.0 for idx in range(buckets)]


HANDCRAFTED_FEATURE_NAMES = [
    "pre_area",
    "spectral_delta",
    "texture_delta",
    "heat_signature",
    "crater_density",
    "std_spectral",
    "max_spectral",
    "high_change_ratio",
    "severe_damage_ratio",
    "collapse_ratio",
    "post_brightness",
    "brightness_drop",
    "normalized_distance",
    "detection_confidence",
    "threat_score",
    "damage_score",
    "change_peak",
    "spectral_x_high_change",
    "max_spectral_x_severe",
    "brightness_drop_plus_spectral",
    "high_change_minus_severe",
] + [f"disaster_bucket_{idx}" for idx in range(10)]


def build_handcrafted_vector(row: dict) -> List[float]:
    spectral = as_float(row, ["spectral_delta", "delta_spectral", "mean_abs_diff", "change_score"], 0.0)
    texture = as_float(row, ["texture_delta", "delta_texture", "edge_change"], 0.0)
    heat = as_float(row, ["heat_signature", "thermal_delta", "brightness_delta"], 0.0)
    crater = as_float(row, ["crater_density", "debris_density", "damage_texture"], 0.0)
    pre_area = as_float(row, ["pre_area", "area_norm", "building_area_norm", "area"], 0.5)
    distance = as_float(row, ["normalized_distance", "distance_norm", "distance_to_center"], 0.5)
    det_conf = as_float(row, ["detection_confidence", "det_conf", "confidence"], 0.8)
    threat = as_float(row, ["threat_score", "priority_score", "prior_threat"], 0.5)
    std_spectral = as_float(row, ["std_spectral"], texture)
    max_spectral = as_float(row, ["max_spectral"], max(spectral, heat) * 1.15)
    high_change = as_float(row, ["high_change_ratio"], crater * 0.55)
    severe = as_float(row, ["severe_damage_ratio"], crater * 0.32)
    collapse = as_float(row, ["collapse_ratio"], 0.0)
    brightness_drop = as_float(row, ["brightness_drop"], heat * 0.45)
    post_brightness = as_float(row, ["post_brightness"], clamp(0.55 - brightness_drop))
    damage_score = (
        0.16 * spectral
        + 0.22 * high_change
        + 0.18 * max_spectral
        + 0.14 * brightness_drop
        + 0.10 * texture
        + 0.10 * heat
        + 0.10 * severe
    )
    change_peak = max(spectral, max_spectral, high_change, severe)
    return [
        pre_area,
        spectral,
        texture,
        heat,
        crater,
        std_spectral,
        max_spectral,
        high_change,
        severe,
        collapse,
        post_brightness,
        brightness_drop,
        distance,
        det_conf,
        threat,
        damage_score,
        change_peak,
        spectral * high_change,
        max_spectral * severe,
        brightness_drop + spectral,
        high_change - severe,
    ] + disaster_bucket_features(row.get("sample_id") or row.get("target_id") or "")


HANDCRAFTED_DIM = len(HANDCRAFTED_FEATURE_NAMES)

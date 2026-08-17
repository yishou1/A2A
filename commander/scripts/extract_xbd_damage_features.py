#!/usr/bin/env python3
import argparse
import csv
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from PIL import Image, ImageDraw


_POINT_RE = re.compile(r"[-+]?(?:\d+\.\d+|\d+)")


def _parse_polygon_wkt(wkt: str) -> List[Tuple[float, float]]:
    """Parse the exterior ring of a simple xBD POLYGON WKT."""
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


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _image_pairs(images_dir: Path) -> Dict[str, Dict[str, Path]]:
    pairs: Dict[str, Dict[str, Path]] = {}
    for path in images_dir.glob("*.png"):
        name = path.name
        if name.endswith("_pre_disaster.png"):
            sample_id = name[: -len("_pre_disaster.png")]
            pairs.setdefault(sample_id, {})["pre"] = path
        elif name.endswith("_post_disaster.png"):
            sample_id = name[: -len("_post_disaster.png")]
            pairs.setdefault(sample_id, {})["post"] = path
    return pairs


def _label_path(labels_dir: Path, sample_id: str, phase: str = "post") -> Path:
    return labels_dir / f"{sample_id}_{phase}_disaster.json"


def _load_buildings(label_path: Path) -> List[dict]:
    data = json.loads(label_path.read_text(encoding="utf-8"))
    buildings = []
    for feature in data.get("features", {}).get("xy", []):
        props = feature.get("properties", {})
        if props.get("feature_type") != "building":
            continue
        polygon = _parse_polygon_wkt(feature.get("wkt", ""))
        if len(polygon) >= 3:
            buildings.append({"properties": props, "polygon": polygon})
    return buildings


def _bbox(points: List[Tuple[float, float]], width: int, height: int) -> Optional[Tuple[int, int, int, int]]:
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


def _polygon_features(
    pre_image: Image.Image,
    post_image: Image.Image,
    polygon: List[Tuple[float, float]],
) -> Optional[dict]:
    width, height = pre_image.size
    box = _bbox(polygon, width, height)
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
        "pre_area": _clamp(area / float(width * height)),
        "spectral_delta": _clamp(mean_spectral),
        "texture_delta": _clamp(texture_sum / area),
        "heat_signature": _clamp(brightness_sum / area),
        "crater_density": _clamp(dark_or_changed / float(area)),
        "std_spectral": _clamp(math.sqrt(spectral_variance)),
        "max_spectral": _clamp(max_spectral),
        "high_change_ratio": _clamp(high_change / float(area)),
        "severe_damage_ratio": _clamp(severe_damage / float(area)),
        "collapse_ratio": _clamp(collapse / float(area)),
        "post_brightness": _clamp(post_brightness_mean),
        "brightness_drop": _clamp(max(0.0, pre_brightness_mean - post_brightness_mean)),
        "normalized_distance": _clamp(center_dist / max_dist),
        "centroid_x": cx,
        "centroid_y": cy,
        "pixel_area": area,
    }


def _damage_label(subtype: str) -> int:
    normalized = (subtype or "").strip().lower()
    if normalized in {"", "no-damage"}:
        return 0
    if normalized in {"un-classified", "unclassified"}:
        return -1
    return 1


def _severity_label(value: Any) -> int:
    raw = str(value or "").strip().lower().replace("_", "-")
    if raw in {"un-classified", "unclassified"}:
        return -1
    severity_map = {
        "": 0,
        "no-damage": 0,
        "minor-damage": 1,
        "major-damage": 2,
        "destroyed": 3,
        "minor": 1,
        "major": 2,
    }
    if raw in severity_map:
        return severity_map[raw]
    try:
        numeric = int(float(raw))
        if 0 <= numeric <= 3:
            return numeric
    except (TypeError, ValueError):
        pass
    binary = _damage_label(raw)
    if binary < 0:
        return -1
    return 0 if binary == 0 else 1


def extract_features(input_root: Path, output_csv: Path, report_json: Path) -> dict:
    images_dir = input_root / "images"
    labels_dir = input_root / "labels"
    if not images_dir.exists() or not labels_dir.exists():
        raise FileNotFoundError(f"Expected images/ and labels/ under {input_root}")

    rows: List[dict] = []
    skipped = 0
    pairs = _image_pairs(images_dir)
    for sample_index, (sample_id, pair) in enumerate(sorted(pairs.items())):
        pre_path = pair.get("pre")
        post_path = pair.get("post")
        post_label = _label_path(labels_dir, sample_id, "post")
        if not pre_path or not post_path or not post_label.exists():
            skipped += 1
            continue
        pre_image = Image.open(pre_path).convert("RGB")
        post_image = Image.open(post_path).convert("RGB")
        for index, building in enumerate(_load_buildings(post_label)):
            props = building["properties"]
            subtype = str(props.get("subtype") or "")
            severity_label = _severity_label(subtype)
            if severity_label < 0:
                skipped += 1
                continue
            damage_label = 0 if severity_label == 0 else 1
            damage_tier = 0 if severity_label <= 0 else (1 if severity_label == 1 else 2)
            features = _polygon_features(pre_image, post_image, building["polygon"])
            if features is None:
                skipped += 1
                continue
            rows.append(
                {
                    "sample_id": sample_id,
                    "building_index": index,
                    "uid": props.get("uid", ""),
                    "subtype": subtype,
                    "pre_area": features["pre_area"],
                    "spectral_delta": features["spectral_delta"],
                    "texture_delta": features["texture_delta"],
                    "heat_signature": features["heat_signature"],
                    "crater_density": features["crater_density"],
                    "std_spectral": features["std_spectral"],
                    "max_spectral": features["max_spectral"],
                    "high_change_ratio": features["high_change_ratio"],
                    "severe_damage_ratio": features["severe_damage_ratio"],
                    "collapse_ratio": features["collapse_ratio"],
                    "post_brightness": features["post_brightness"],
                    "brightness_drop": features["brightness_drop"],
                    "normalized_distance": features["normalized_distance"],
                    "detection_confidence": 1.0,
                    "threat_score": 0.5,
                    "severity_label": severity_label,
                    "damage_tier": damage_tier,
                    "damage_label": damage_label,
                    "centroid_x": features["centroid_x"],
                    "centroid_y": features["centroid_y"],
                    "pixel_area": features["pixel_area"],
                }
            )
        if sample_index > 0 and sample_index % 100 == 0:
            print(f"processed {sample_index} image pairs, building rows={len(rows)}", flush=True)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "sample_id",
        "building_index",
        "uid",
        "subtype",
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
        "severity_label",
        "damage_tier",
        "damage_label",
        "centroid_x",
        "centroid_y",
        "pixel_area",
    ]
    with output_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    label_counts = Counter(str(row["damage_label"]) for row in rows)
    tier_counts = Counter(str(row["damage_tier"]) for row in rows)
    severity_counts = Counter(str(row["severity_label"]) for row in rows)
    subtype_counts = Counter(str(row["subtype"]) for row in rows)
    report = {
        "input_root": str(input_root),
        "output_csv": str(output_csv),
        "sample_pairs": sum(1 for pair in pairs.values() if pair.get("pre") and pair.get("post")),
        "building_rows": len(rows),
        "skipped_items": skipped,
        "damage_label_counts": dict(label_counts),
        "damage_tier_counts": dict(tier_counts),
        "severity_label_counts": dict(severity_counts),
        "subtype_counts": dict(subtype_counts),
        "fieldnames": fieldnames,
        "note": "This is an xBD sample feature table. It is suitable for pipeline verification; full benchmark metrics require a larger train/test split.",
    }
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract tabular damage features from xBD images and post-disaster labels.")
    parser.add_argument("--input-root", default="data/xbd/train", help="Directory containing images/ and labels/.")
    parser.add_argument("--output-csv", default="data/xbd/processed/xbd_damage_features.csv", help="Output CSV path.")
    parser.add_argument("--report-json", default="data/xbd/processed/xbd_damage_features_report.json", help="Output report JSON path.")
    args = parser.parse_args()

    report = extract_features(Path(args.input_root), Path(args.output_csv), Path(args.report_json))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build the packaged Taiwan southeast relief overlays from NOAA ETOPO 2022.

The generated PNG and JSON files are the only artifacts used at runtime.  The
source GeoTIFF is a build-time input and is intentionally not copied into the
application package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "static" / "assets" / "maps" / "taiwan-se-relief"
SOURCE_URL = (
    "https://www.ngdc.noaa.gov/mgg/global/relief/ETOPO2022/data/15s/"
    "15s_surface_elev_gtif/ETOPO_2022_v1_15s_N30E120_surface.tif"
)
SOURCE_SHA256 = "5b46d290694fb0b5019b800334c6eb42157fe4fea0bc0b253c54424db924bb70"
BOUNDS = {"south": 21.35, "west": 120.35, "north": 23.35, "east": 122.05}
OUTPUT_SIZE = (1632, 2048)

COLOR_STOPS = (
    (-9000, (3, 11, 27)),
    (-6000, (4, 20, 43)),
    (-4000, (6, 34, 63)),
    (-2500, (8, 50, 78)),
    (-1500, (10, 67, 94)),
    (-750, (14, 87, 109)),
    (-300, (25, 111, 124)),
    (-100, (43, 132, 136)),
    (0, (61, 104, 72)),
    (250, (79, 114, 67)),
    (750, (111, 119, 70)),
    (1500, (137, 120, 80)),
    (2500, (158, 143, 111)),
    (3500, (191, 188, 162)),
    (4500, (226, 226, 211)),
)
CONTOUR_LEVELS = (-6000, -4000, -3000, -2000, -1000, -500, -200, 0, 250, 500, 1000, 2000, 3000)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _ensure_source(path: Path, allow_download: bool) -> None:
    if not path.exists():
        if not allow_download:
            raise SystemExit(f"source GeoTIFF is missing: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {SOURCE_URL}")
        urllib.request.urlretrieve(SOURCE_URL, path)
    actual = _sha256(path)
    if actual != SOURCE_SHA256:
        raise SystemExit(f"source checksum mismatch: expected {SOURCE_SHA256}, got {actual}")


def _crop_source(path: Path) -> np.ndarray:
    # The official tile is EPSG:4326, 15 arc-second, 120E..135E and 15N..30N.
    tile_west, tile_north = 120.0, 30.0
    cell = 15.0 / 3600.0
    left = round((BOUNDS["west"] - tile_west) / cell)
    right = round((BOUNDS["east"] - tile_west) / cell)
    top = round((tile_north - BOUNDS["north"]) / cell)
    bottom = round((tile_north - BOUNDS["south"]) / cell)
    with Image.open(path) as source:
        crop = source.crop((left, top, right, bottom))
        values = np.asarray(crop, dtype=np.float32)
    if values.ndim != 2 or not np.isfinite(values).all():
        raise SystemExit("source relief crop is invalid")
    return values


def _resize_float(values: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    image = Image.fromarray(values, mode="F")
    return np.asarray(image.resize(size, Image.Resampling.BICUBIC), dtype=np.float32)


def _colorize(values: np.ndarray) -> Image.Image:
    levels = np.array([row[0] for row in COLOR_STOPS], dtype=np.float32)
    colors = np.array([row[1] for row in COLOR_STOPS], dtype=np.float32)
    channels = [np.interp(values, levels, colors[:, index]) for index in range(3)]
    rgb = np.clip(np.stack(channels, axis=-1), 0, 255).astype(np.uint8)
    return Image.fromarray(rgb, mode="RGB")


def _hillshade(values: np.ndarray) -> Image.Image:
    # Pixel dimensions vary slightly with latitude.  The ratio is sufficient
    # for visual hillshade and does not alter the underlying elevation values.
    dy, dx = np.gradient(values)
    latitude = (BOUNDS["south"] + BOUNDS["north"]) / 2
    dx /= max(0.2, np.cos(np.deg2rad(latitude)))
    exaggeration = 2.1
    slope = np.pi / 2.0 - np.arctan(np.hypot(dx, dy) * exaggeration / 240.0)
    aspect = np.arctan2(-dx, dy)
    azimuth = np.deg2rad(315.0)
    altitude = np.deg2rad(42.0)
    light = (
        np.sin(altitude) * np.sin(slope)
        + np.cos(altitude) * np.cos(slope) * np.cos(azimuth - aspect)
    )
    light = np.clip((light + 1.0) / 2.0, 0.0, 1.0)
    rgba = np.zeros((*values.shape, 4), dtype=np.uint8)
    shadow = light < 0.56
    highlight = ~shadow
    rgba[shadow, :3] = (2, 7, 13)
    rgba[shadow, 3] = np.clip((0.56 - light[shadow]) * 360.0, 0, 118).astype(np.uint8)
    rgba[highlight, :3] = (220, 232, 220)
    rgba[highlight, 3] = np.clip((light[highlight] - 0.56) * 105.0, 0, 32).astype(np.uint8)
    return Image.fromarray(rgba, mode="RGBA")


def _contours(values: np.ndarray) -> Image.Image:
    rgba = np.zeros((*values.shape, 4), dtype=np.uint8)
    for level in CONTOUR_LEVELS:
        horizontal = (values[:, :-1] - level) * (values[:, 1:] - level) <= 0
        vertical = (values[:-1, :] - level) * (values[1:, :] - level) <= 0
        mask = np.zeros(values.shape, dtype=bool)
        mask[:, :-1] |= horizontal
        mask[:-1, :] |= vertical
        # One-pixel dilation keeps contours legible on high-DPI screens.
        mask[1:, :] |= mask[:-1, :]
        mask[:, 1:] |= mask[:, :-1]
        if level < 0:
            color = (105, 190, 211, 92 if level not in {-1000, -3000} else 132)
        elif level == 0:
            color = (218, 226, 207, 185)
        else:
            color = (224, 200, 143, 92 if level not in {500, 2000} else 132)
        rgba[mask] = color
    return Image.fromarray(rgba, mode="RGBA")


def build(source: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    native = _crop_source(source)
    values = _resize_float(native, OUTPUT_SIZE)
    terrain_path = output / "terrain-bathymetry.png"
    hillshade_path = output / "hillshade.png"
    contours_path = output / "contours.png"
    _colorize(values).save(terrain_path, optimize=True)
    _hillshade(values).save(hillshade_path, optimize=True)
    _contours(values).save(contours_path, optimize=True)
    manifest = {
        "schema_version": "amos.offline-relief.v1",
        "pack_id": "taiwan-se-etopo2022-v1",
        "bounds": BOUNDS,
        "native_resolution_arc_seconds": 15,
        "render_size": {"width": OUTPUT_SIZE[0], "height": OUTPUT_SIZE[1]},
        "elevation_range_m": {
            "minimum": round(float(values.min())),
            "maximum": round(float(values.max())),
        },
        "layers": {
            "terrain": {"path": "terrain-bathymetry.png", "default_visible": True},
            "hillshade": {"path": "hillshade.png", "default_visible": True},
            "contours": {"path": "contours.png", "default_visible": True},
        },
        "legend": {
            "land_m": [0, 500, 1500, 3000],
            "sea_m": [0, -200, -1000, -3000, -6000],
        },
        "source": {
            "name": "NOAA NCEI ETOPO 2022 15 Arc-Second Global Relief Model",
            "url": "https://www.ncei.noaa.gov/products/etopo-global-relief-model",
            "doi": "10.25921/fd45-gt74",
            "source_tile": "ETOPO_2022_v1_15s_N30E120_surface.tif",
            "source_sha256": SOURCE_SHA256,
        },
        "runtime_network_required": False,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Built offline relief pack in {output}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("/tmp/ETOPO_2022_v1_15s_N30E120_surface.tif"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--download", action="store_true", help="download the official build-time source if absent")
    args = parser.parse_args()
    _ensure_source(args.source, args.download)
    build(args.source, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

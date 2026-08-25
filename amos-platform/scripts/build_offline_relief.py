#!/usr/bin/env python3
"""Build the packaged Taiwan/Western Pacific relief overlays from NOAA ETOPO 2022.

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
SOURCE_BASE_URL = (
    "https://www.ngdc.noaa.gov/mgg/global/relief/ETOPO2022/data/15s/"
    "15s_surface_elev_gtif"
)
SOURCE_TILES = (
    ("N45E105", 105.0, 45.0, "0054f4d258a9678ba55988b408a936d05de95e6e6d430c2a7ea123efc2cec7e0"),
    ("N45E120", 120.0, 45.0, "6468864758e6eab87a6d5349280d5eab1764a1e239d8e944159b6eeb2396bf9a"),
    ("N30E105", 105.0, 30.0, "00c261fe202e85fa20946c40f113024101784d3c555a2112774d54b404d5f0ea"),
    ("N30E120", 120.0, 30.0, "5b46d290694fb0b5019b800334c6eb42157fe4fea0bc0b253c54424db924bb70"),
    ("N15E105", 105.0, 15.0, "d2cb7e439d3d85805ea9f55f5c873286e73c63dcb8de6e2b3e16fb6e8882cbd0"),
    ("N15E120", 120.0, 15.0, "219d5232373f911caac573fa82c1f39f7277041ad89cc547ab5cba193aa5b495"),
)
BOUNDS = {"south": 8.0, "west": 105.0, "north": 35.0, "east": 135.0}
OUTPUT_SIZE = (6144, 5530)
DETAIL_BOUNDS = {"south": 18.0, "west": 120.0, "north": 28.0, "east": 127.0}
DETAIL_SIZE = (3584, 5120)
CELL_DEGREES = 15.0 / 3600.0

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


def _tile_filename(tile_id: str) -> str:
    return f"ETOPO_2022_v1_15s_{tile_id}_surface.tif"


def _ensure_sources(source_dir: Path, allow_download: bool) -> list[Path]:
    paths = []
    for tile_id, _west, _north, expected_sha256 in SOURCE_TILES:
        filename = _tile_filename(tile_id)
        path = source_dir / filename
        if not path.exists():
            if not allow_download:
                raise SystemExit(f"source GeoTIFF is missing: {path}")
            source_dir.mkdir(parents=True, exist_ok=True)
            url = f"{SOURCE_BASE_URL}/{filename}"
            print(f"Downloading {url}")
            urllib.request.urlretrieve(url, path)
        actual = _sha256(path)
        if actual != expected_sha256:
            raise SystemExit(
                f"source checksum mismatch for {filename}: expected {expected_sha256}, got {actual}"
            )
        paths.append(path)
    return paths


def _mosaic_sources(paths: list[Path]) -> np.ndarray:
    width = round((BOUNDS["east"] - BOUNDS["west"]) / CELL_DEGREES)
    height = round((BOUNDS["north"] - BOUNDS["south"]) / CELL_DEGREES)
    values = np.full((height, width), np.nan, dtype=np.float32)

    for path, (_tile_id, tile_west, tile_north, _sha256_value) in zip(paths, SOURCE_TILES):
        tile_east = tile_west + 15.0
        tile_south = tile_north - 15.0
        overlap_west = max(BOUNDS["west"], tile_west)
        overlap_east = min(BOUNDS["east"], tile_east)
        overlap_south = max(BOUNDS["south"], tile_south)
        overlap_north = min(BOUNDS["north"], tile_north)
        if overlap_west >= overlap_east or overlap_south >= overlap_north:
            continue

        source_box = (
            round((overlap_west - tile_west) / CELL_DEGREES),
            round((tile_north - overlap_north) / CELL_DEGREES),
            round((overlap_east - tile_west) / CELL_DEGREES),
            round((tile_north - overlap_south) / CELL_DEGREES),
        )
        target_box = (
            round((overlap_west - BOUNDS["west"]) / CELL_DEGREES),
            round((BOUNDS["north"] - overlap_north) / CELL_DEGREES),
            round((overlap_east - BOUNDS["west"]) / CELL_DEGREES),
            round((BOUNDS["north"] - overlap_south) / CELL_DEGREES),
        )
        with Image.open(path) as source:
            crop = np.asarray(source.crop(source_box), dtype=np.float32)
        left, top, right, bottom = target_box
        values[top:bottom, left:right] = crop

    if not np.isfinite(values).all():
        raise SystemExit("source relief mosaic has uncovered or invalid cells")
    return values


def _mercator_y(latitude: float | np.ndarray) -> float | np.ndarray:
    radians = np.deg2rad(latitude)
    return np.log(np.tan(np.pi / 4.0 + radians / 2.0))


def _resize_web_mercator(
    values: np.ndarray,
    bounds: dict[str, float],
    size: tuple[int, int],
) -> np.ndarray:
    """Resample an EPSG:4326 grid for affine display in a Leaflet image overlay."""
    width, height = size
    horizontal = np.asarray(
        Image.fromarray(values, mode="F").resize(
            (width, values.shape[0]),
            Image.Resampling.BICUBIC,
        ),
        dtype=np.float32,
    )

    north_y = _mercator_y(bounds["north"])
    south_y = _mercator_y(bounds["south"])
    fractions = (np.arange(height, dtype=np.float64) + 0.5) / height
    projected_y = north_y + fractions * (south_y - north_y)
    latitudes = np.rad2deg(np.arctan(np.sinh(projected_y)))
    source_rows = (
        (bounds["north"] - latitudes)
        / (bounds["north"] - bounds["south"])
        * values.shape[0]
        - 0.5
    )
    source_rows = np.clip(source_rows, 0.0, values.shape[0] - 1.0)
    row_before = np.floor(source_rows).astype(np.int32)
    row_after = np.minimum(row_before + 1, values.shape[0] - 1)
    weights = (source_rows - row_before).astype(np.float32)

    projected = np.empty((height, width), dtype=np.float32)
    for row in range(height):
        projected[row] = (
            horizontal[row_before[row]] * (1.0 - weights[row])
            + horizontal[row_after[row]] * weights[row]
        )
    return projected


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


def _write_layers(values: np.ndarray, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    _colorize(values).save(output / "terrain-bathymetry.png", optimize=True)
    _hillshade(values).save(output / "hillshade.png", optimize=True)
    _contours(values).save(output / "contours.png", optimize=True)


def _crop_mosaic(values: np.ndarray, bounds: dict[str, float]) -> np.ndarray:
    left = round((bounds["west"] - BOUNDS["west"]) / CELL_DEGREES)
    right = round((bounds["east"] - BOUNDS["west"]) / CELL_DEGREES)
    top = round((BOUNDS["north"] - bounds["north"]) / CELL_DEGREES)
    bottom = round((BOUNDS["north"] - bounds["south"]) / CELL_DEGREES)
    return values[top:bottom, left:right]


def build(sources: list[Path], output: Path) -> None:
    native = _mosaic_sources(sources)
    values = _resize_web_mercator(native, BOUNDS, OUTPUT_SIZE)
    detail_values = _resize_web_mercator(
        _crop_mosaic(native, DETAIL_BOUNDS),
        DETAIL_BOUNDS,
        DETAIL_SIZE,
    )
    _write_layers(values, output)
    _write_layers(detail_values, output / "detail")
    manifest = {
        "schema_version": "amos.offline-relief.v1",
        "pack_id": "east-asia-western-pacific-etopo2022-v3",
        "projection": "EPSG:3857",
        "cache_version": "20260825c",
        "bounds": BOUNDS,
        "native_resolution_arc_seconds": 15,
        "render_size": {"width": OUTPUT_SIZE[0], "height": OUTPUT_SIZE[1]},
        "elevation_range_m": {
            "minimum": round(float(values.min())),
            "maximum": round(float(values.max())),
        },
        "layers": {
            "terrain": {
                "path": "terrain-bathymetry.png",
                "kind": "terrain",
                "max_zoom": 8,
                "default_visible": True,
            },
            "hillshade": {
                "path": "hillshade.png",
                "kind": "hillshade",
                "max_zoom": 8,
                "default_visible": True,
            },
            "contours": {
                "path": "contours.png",
                "kind": "contours",
                "max_zoom": 8,
                "default_visible": True,
            },
            "terrain_detail": {
                "path": "detail/terrain-bathymetry.png",
                "kind": "terrain",
                "bounds": DETAIL_BOUNDS,
                "render_size": {"width": DETAIL_SIZE[0], "height": DETAIL_SIZE[1]},
                "min_zoom": 9,
                "default_visible": True,
            },
            "hillshade_detail": {
                "path": "detail/hillshade.png",
                "kind": "hillshade",
                "bounds": DETAIL_BOUNDS,
                "render_size": {"width": DETAIL_SIZE[0], "height": DETAIL_SIZE[1]},
                "min_zoom": 9,
                "default_visible": True,
            },
            "contours_detail": {
                "path": "detail/contours.png",
                "kind": "contours",
                "bounds": DETAIL_BOUNDS,
                "render_size": {"width": DETAIL_SIZE[0], "height": DETAIL_SIZE[1]},
                "min_zoom": 9,
                "default_visible": True,
            },
        },
        "legend": {
            "land_m": [0, 500, 1500, 3000],
            "sea_m": [0, -200, -1000, -3000, -6000],
        },
        "source": {
            "name": "NOAA NCEI ETOPO 2022 15 Arc-Second Global Relief Model",
            "url": "https://www.ncei.noaa.gov/products/etopo-global-relief-model",
            "doi": "10.25921/fd45-gt74",
            "source_tiles": [
                {"name": _tile_filename(tile_id), "sha256": sha256_value}
                for tile_id, _west, _north, sha256_value in SOURCE_TILES
            ],
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
    parser.add_argument("--source-dir", type=Path, default=Path("/tmp"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--download", action="store_true", help="download the official build-time source if absent")
    args = parser.parse_args()
    sources = _ensure_sources(args.source_dir, args.download)
    build(sources, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

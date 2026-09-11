#!/usr/bin/env python3
"""Reproject the public-domain Natural Earth II GeoTIFF into offline XYZ tiles.

Input is the unmodified NE2_HR_LC_SR_W release (EPSG:4326). No runtime
network requests, terrain blending, or browser brightness filters are needed.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = 300_000_000
BOUNDS = {"west": 105, "south": 8, "east": 135, "north": 35}


def tile_y(latitude, zoom):
    return (1 - math.asinh(math.tan(math.radians(latitude))) / math.pi) / 2 * 2**zoom


def build(source, output):
    output.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image.load()
        width, height = image.size
        count = 0
        for zoom in range(5, 10):
            n = 2**zoom
            xs = range(math.floor((BOUNDS["west"] + 180) / 360 * n), math.ceil((BOUNDS["east"] + 180) / 360 * n))
            ys = range(math.floor(tile_y(BOUNDS["north"], zoom)), math.ceil(tile_y(BOUNDS["south"], zoom)))
            for x in xs:
                folder = output / str(zoom) / str(x)
                folder.mkdir(parents=True, exist_ok=True)
                for y in ys:
                    # Pixel centers are transformed from Web Mercator to the
                    # original plate-carree grid. Bilinear sampling is continuous
                    # across tile boundaries; no separately stretched overlays.
                    px = (x + (np.arange(256) + .5) / 256) / n * width - .5
                    lat = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * (y + (np.arange(256) + .5) / 256) / n))))
                    py = (90 - lat) / 180 * height - .5
                    left, top = int(np.floor(px.min())), int(np.floor(py.min()))
                    right, bottom = int(np.floor(px.max())) + 2, int(np.floor(py.max())) + 2
                    patch = np.asarray(image.crop((left, top, right, bottom)), dtype=np.float32)
                    ix, iy = np.floor(px).astype(int) - left, np.floor(py).astype(int) - top
                    fx, fy = (px - np.floor(px))[None, :, None], (py - np.floor(py))[:, None, None]
                    a = patch[iy[:, None], ix[None, :]] * (1 - fx) + patch[iy[:, None], ix[None, :] + 1] * fx
                    b = patch[iy[:, None] + 1, ix[None, :]] * (1 - fx) + patch[iy[:, None] + 1, ix[None, :] + 1] * fx
                    pixels = np.clip(a * (1 - fy) + b * fy, 0, 255).astype("uint8")
                    # Cartographic water palette: retain the source relief
                    # shading, but use blue-grey ink colors for the maritime
                    # presentation. This is not a quantitative depth encoding.
                    rgb = pixels.astype(np.float32)
                    water = (rgb[:, :, 2] > rgb[:, :, 0] + 12) & (rgb[:, :, 1] > rgb[:, :, 0] + 7)
                    shade = (rgb[:, :, 0] * .2126 + rgb[:, :, 1] * .7152 + rgb[:, :, 2] * .0722) / 255
                    for channel, (base, scale) in enumerate(((12, 46), (30, 76), (47, 94))):
                        pixels[:, :, channel][water] = np.clip(base + scale * shade[water], 0, 255).astype("uint8")
                    Image.fromarray(pixels).save(folder / f"{y}.webp", quality=90, method=4)
                    count += 1
            print(f"z{zoom}: {count} tiles total", flush=True)
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    manifest = {
        "tile_pack_id": "natural-earth-ii-western-pacific-v1",
        "format": "xyz-webp", "projection": "EPSG:3857", "bounds": BOUNDS,
        "min_zoom": 5, "max_zoom": 9, "tile_count": count,
        "url": "/static/tiles/natural-terrain/{z}/{x}/{y}.webp",
        "runtime_network_required": False,
        "cartography": "Natural land colors; blue-grey water palette retaining source relief shading. Colors do not encode numerical depths.",
        "source": {"name": "Natural Earth II with Shaded Relief and Water", "version": "3.2.0",
                   "url": "https://naciscdn.org/naturalearth/10m/raster/NE2_HR_LC_SR_W.zip",
                   "license": "Public domain", "sha256": digest.hexdigest(),
                   "dimensions": [width, height],
                   "resolution_note": "Regional overview cartography, approximately 1.85 km per source pixel at equator; display zoom does not add source detail."},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.source, args.output)

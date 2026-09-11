#!/usr/bin/env python3
"""Build an offline natural-color hillshade basemap from Mapzen Terrarium DEM.

Only build time accesses AWS Open Data. DEM samples are decoded before shading;
adjacent source pixels supply a one-pixel halo to avoid XYZ edge artifacts.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import threading
import time

import numpy as np
from PIL import Image
import requests

BOUNDS = {"west": 119, "south": 17, "east": 124, "north": 25}
SOURCE = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
LOCAL = threading.local()
SEA = [(-11000, (17, 36, 55)), (-6000, (22, 46, 68)), (-3000, (28, 57, 80)), (-1000, (35, 70, 90)), (-200, (44, 82, 98)), (0, (51, 91, 105))]
LAND = [(0, (90, 113, 83)), (300, (105, 124, 91)), (800, (124, 135, 102)), (1500, (145, 140, 112)), (2500, (165, 155, 131)), (4000, (191, 185, 168)), (9000, (220, 216, 204))]


def ranges(zoom):
    n = 2 ** zoom
    def ty(lat):
        return (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return (range(math.floor((BOUNDS["west"] + 180) / 360 * n), math.ceil((BOUNDS["east"] + 180) / 360 * n)),
            range(math.floor(ty(BOUNDS["north"])), math.ceil(ty(BOUNDS["south"]))))


def download(key, cache):
    z, x, y = key
    path = cache / str(z) / str(x) / f"{y}.png"
    if not path.exists():
        if not hasattr(LOCAL, "session"):
            LOCAL.session = requests.Session()
        for attempt in range(4):
            try:
                response = LOCAL.session.get(SOURCE.format(z=z, x=x, y=y), timeout=25)
                response.raise_for_status()
                path.parent.mkdir(parents=True, exist_ok=True)
                pending = path.with_suffix(".part")
                pending.write_bytes(response.content)
                with Image.open(pending) as image:
                    assert image.size == (256, 256)
                    image.verify()
                pending.replace(path)
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(attempt + 1)
    return {"tile": f"{z}/{x}/{y}", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def build(cache, output):
    keys = []
    for z in range(8, 12):
        xs, ys = ranges(z)
        keys.extend((z, x, y) for x in range(xs.start - 1, xs.stop + 1) for y in range(ys.start - 1, ys.stop + 1))
    checksums = []
    with ThreadPoolExecutor(max_workers=48) as pool:
        futures = [pool.submit(download, key, cache) for key in keys]
        for i, future in enumerate(as_completed(futures), 1):
            checksums.append(future.result())
            if i % 100 == 0 or i == len(keys):
                print(f"DEM cache: {i}/{len(keys)}", flush=True)

    @lru_cache(maxsize=160)
    def heights(z, x, y):
        with Image.open(cache / str(z) / str(x) / f"{y}.png") as image:
            p = np.asarray(image, dtype=np.float32)
        return p[:, :, 0] * 256 + p[:, :, 1] + p[:, :, 2] / 256 - 32768

    count = 0
    for z in range(8, 12):
        xs, ys = ranges(z)
        for x in xs:
            folder = output / str(z) / str(x)
            folder.mkdir(parents=True, exist_ok=True)
            for y in ys:
                halo = np.empty((258, 258), dtype=np.float32)
                halo[1:-1, 1:-1] = heights(z, x, y)
                halo[0, 1:-1] = heights(z, x, y - 1)[-1]
                halo[-1, 1:-1] = heights(z, x, y + 1)[0]
                halo[1:-1, 0] = heights(z, x - 1, y)[:, -1]
                halo[1:-1, -1] = heights(z, x + 1, y)[:, 0]
                halo[0, 0] = halo[0, 1]
                halo[0, -1] = halo[0, -2]
                halo[-1, 0] = halo[-1, 1]
                halo[-1, -1] = halo[-1, -2]
                h = halo[1:-1, 1:-1]
                lat = math.atan(math.sinh(math.pi * (1 - 2 * (y + .5) / 2**z)))
                spacing = 40075016.6856 * math.cos(lat) / (256 * 2**z)
                dx = (halo[1:-1, 2:] - halo[1:-1, :-2]) / (2 * spacing)
                dy = (halo[2:, 1:-1] - halo[:-2, 1:-1]) / (2 * spacing)
                light = np.clip((-.5 * dx + .5 * dy + .7071) / np.sqrt(dx*dx + dy*dy + 1), 0, 1)
                water = h < 0
                rgb = np.empty((256, 256, 3), dtype=np.float32)
                for mask, stops in ((water, SEA), (~water, LAND)):
                    levels = [p[0] for p in stops]
                    for c in range(3):
                        rgb[:, :, c][mask] = np.interp(h[mask], levels, [p[1][c] for p in stops])
                shading = np.where(water, .91 + .13 * light, .63 + .53 * light)
                pixels = np.clip(rgb * shading[:, :, None], 0, 255).astype("uint8")
                Image.fromarray(pixels).save(folder / f"{y}.webp", quality=92, method=4)
                count += 1
        print(f"Rendered z{z}: {count} tiles total", flush=True)
    manifest = {"tile_pack_id": "mapzen-natural-terrain-detail-v1", "bounds": BOUNDS,
                "projection": "EPSG:3857", "format": "xyz-webp", "tile_count": count,
                "min_zoom": 8, "max_zoom": 11, "runtime_network_required": False,
                "source": {"name": "Mapzen Terrain Tiles / AWS Open Data", "url": SOURCE,
                           "attribution": "Mapzen; SRTM and GMTED2010 courtesy of USGS; ETOPO1 by NOAA/NCEI",
                           "license_url": "https://github.com/tilezen/joerd/blob/master/docs/attribution.md"},
                "processing": "Terrarium elevations decoded in meters; natural hypsometric colors and metric hillshade baked into XYZ WebP tiles. Native data resolution varies by source; z11 display grid is approximately 70m at 21N."}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "source-checksums.json").write_text(json.dumps(sorted(checksums, key=lambda r:r["tile"]), indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.cache, args.output)

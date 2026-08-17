"""将 YOLO 数据集中所有图像统一缩放到固定边长（默认 640），避免 RT-DETR batch 内尺寸不一致。

标签为 YOLO 归一化坐标，直接拉伸图像不改变相对框位置。

用法:
  python scripts/resize_yolo_dataset.py --root datasets/battlefield --size 640
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def resize_split(images_dir: Path, size: int) -> int:
    if not images_dir.is_dir():
        return 0
    count = 0
    for img_path in sorted(images_dir.iterdir()):
        if img_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
            continue
        with Image.open(img_path) as im:
            resized = im.convert("RGB").resize((size, size), Image.Resampling.LANCZOS)
            resized.save(img_path)
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Resize YOLO dataset images to fixed square size")
    parser.add_argument("--root", type=Path, default=Path("datasets/battlefield"))
    parser.add_argument("--size", type=int, default=640)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    dataset = args.root if args.root.is_absolute() else root / args.root

    total = 0
    for split in ("train", "val"):
        n = resize_split(dataset / "images" / split, args.size)
        print(f"[{split}] resized {n} images -> {args.size}x{args.size}")
        total += n

    print(f"[DONE] total {total} images at {args.size}x{args.size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

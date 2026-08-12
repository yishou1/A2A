"""
将 DOTA v1.5 训练集转为 YOLO 格式，供 Ultralytics RT-DETR 微调。

默认读取百度网盘常见目录结构：
  D:/BaiduNetdiskDownload/train/
    images/part1.zip, part2.zip, part3.zip
    labelTxt-v1.5/DOTA-v1.5_train_hbb.zip

用法（项目根目录）:
  python scripts/convert_dota_to_yolo.py ^
    --dota-root D:/BaiduNetdiskDownload/train ^
    --output datasets/battlefield

仅转换战场相关 6 类（默认）:
  plane, ship, storage-tank, large-vehicle, small-vehicle, helicopter

转换全部 15 类:
  python scripts/convert_dota_to_yolo.py --dota-root ... --all-classes
"""

from __future__ import annotations

import argparse
import random
import shutil
import zipfile
from pathlib import Path

from PIL import Image

# DOTA v1.5 全部类别
DOTA_V15_CLASSES = [
    "plane",
    "ship",
    "storage-tank",
    "baseball-diamond",
    "tennis-court",
    "basketball-court",
    "ground-track-field",
    "harbor",
    "bridge",
    "large-vehicle",
    "small-vehicle",
    "helicopter",
    "roundabout",
    "soccer-ball-field",
    "swimming-pool",
]

# 与 Agent 地理解算较贴近的子集（可改）
BATTLEFIELD_CLASSES = [
    "plane",
    "ship",
    "storage-tank",
    "large-vehicle",
    "small-vehicle",
    "helicopter",
]


def _extract_zip(zip_path: Path, dest: Path) -> None:
    if dest.is_dir() and any(dest.iterdir()):
        print(f"[skip] already extracted: {dest}")
        return
    dest.mkdir(parents=True, exist_ok=True)
    print(f"[extract] {zip_path.name} -> {dest}")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)


def _ensure_dota_layout(dota_root: Path) -> tuple[Path, Path]:
    """解压图像与 HBB 标注，返回 (images_dir, labels_dir)。"""
    images_dir = dota_root / "_extracted" / "images"
    labels_dir = dota_root / "_extracted" / "labels_hbb"

    img_zip_dir = dota_root / "images"
    label_zip = dota_root / "labelTxt-v1.5" / "DOTA-v1.5_train_hbb.zip"
    if not label_zip.is_file():
        label_zip = dota_root / "labelTxt-v1.5" / "DOTA-v1.5_train.zip"

    if not img_zip_dir.is_dir():
        raise FileNotFoundError(f"未找到图像目录: {img_zip_dir}")
    if not label_zip.is_file():
        raise FileNotFoundError(f"未找到标注 zip: {label_zip}")

    for part in sorted(img_zip_dir.glob("part*.zip")):
        _extract_zip(part, images_dir)

    # part*.zip 解压后可能在 images/images/
    nested = images_dir / "images"
    if nested.is_dir():
        images_dir = nested

    _extract_zip(label_zip, labels_dir)
    return images_dir, labels_dir


def _parse_dota_lines(label_path: Path) -> list[tuple[str, list[float]]]:
    """解析 DOTA 标注行，返回 [(class_name, [x1..y4]), ...]。"""
    items: list[tuple[str, list[float]]] = []
    for raw in label_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("imagesource:") or line.startswith("gsd:"):
            continue
        parts = line.split()
        if len(parts) < 10:
            continue
        try:
            coords = [float(parts[i]) for i in range(8)]
        except ValueError:
            continue
        class_name = parts[8]
        items.append((class_name, coords))
    return items


def _obb_to_yolo_line(
    coords: list[float],
    img_w: int,
    img_h: int,
    class_id: int,
) -> str | None:
    xs = coords[0::2]
    ys = coords[1::2]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if xmax <= xmin or ymax <= ymin:
        return None
    cx = (xmin + xmax) / 2.0 / img_w
    cy = (ymin + ymax) / 2.0 / img_h
    bw = (xmax - xmin) / img_w
    bh = (ymax - ymin) / img_h
    if not (0 < cx < 1 and 0 < cy < 1 and bw > 0 and bh > 0):
        return None
    return f"{class_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


def _write_data_yaml(output: Path, class_names: list[str]) -> None:
    content = "\n".join(
        [
            f"path: {output.resolve().as_posix()}",
            "train: images/train",
            "val: images/val",
            "",
            f"nc: {len(class_names)}",
            "names:",
            *[f"  {i}: {name}" for i, name in enumerate(class_names)],
            "",
        ]
    )
    (output / "data.yaml").write_text(content, encoding="utf-8")


def convert(
    *,
    dota_root: Path,
    output: Path,
    class_names: list[str],
    val_ratio: float = 0.1,
    seed: int = 42,
) -> dict[str, int]:
    images_dir, labels_dir = _ensure_dota_layout(dota_root)
    class_to_id = {name: i for i, name in enumerate(class_names)}

    for split in ("train", "val"):
        (output / "images" / split).mkdir(parents=True, exist_ok=True)
        (output / "labels" / split).mkdir(parents=True, exist_ok=True)

    image_paths = sorted(
        p
        for p in images_dir.rglob("*")
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
    )
    if not image_paths:
        raise RuntimeError(f"解压后未找到图像: {images_dir}")

    rng = random.Random(seed)
    rng.shuffle(image_paths)
    val_count = max(1, int(len(image_paths) * val_ratio))
    val_set = set(image_paths[:val_count])

    stats = {"images": 0, "boxes": 0, "skipped_no_label": 0, "skipped_empty": 0}

    for img_path in image_paths:
        label_path = labels_dir / f"{img_path.stem}.txt"
        if not label_path.is_file():
            stats["skipped_no_label"] += 1
            continue

        with Image.open(img_path) as im:
            img_w, img_h = im.size

        yolo_lines: list[str] = []
        for class_name, coords in _parse_dota_lines(label_path):
            if class_name not in class_to_id:
                continue
            line = _obb_to_yolo_line(coords, img_w, img_h, class_to_id[class_name])
            if line:
                yolo_lines.append(line)

        if not yolo_lines:
            stats["skipped_empty"] += 1
            continue

        split = "val" if img_path in val_set else "train"
        out_img = output / "images" / split / img_path.name
        out_lbl = output / "labels" / split / f"{img_path.stem}.txt"
        shutil.copy2(img_path, out_img)
        out_lbl.write_text("\n".join(yolo_lines) + "\n", encoding="utf-8")
        stats["images"] += 1
        stats["boxes"] += len(yolo_lines)

    _write_data_yaml(output, class_names)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="DOTA -> YOLO for RT-DETR training")
    parser.add_argument(
        "--dota-root",
        type=Path,
        default=Path(r"D:/BaiduNetdiskDownload/train"),
        help="DOTA 下载根目录（含 images/ 与 labelTxt-v1.5/）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("datasets/battlefield"),
        help="YOLO 数据集输出目录（相对项目根）",
    )
    parser.add_argument("--all-classes", action="store_true", help="使用 DOTA 全部 15 类")
    parser.add_argument("--val-ratio", type=float, default=0.1)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    output = args.output if args.output.is_absolute() else root / args.output
    class_names = DOTA_V15_CLASSES if args.all_classes else BATTLEFIELD_CLASSES

    print(f"==> DOTA root: {args.dota_root}")
    print(f"==> output:    {output}")
    print(f"==> classes:   {class_names}")

    stats = convert(
        dota_root=args.dota_root,
        output=output,
        class_names=class_names,
        val_ratio=args.val_ratio,
    )
    print("\n[DONE]")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"\n下一步: python scripts/train_battlefield_rtdetr.py --data {output / 'data.yaml'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

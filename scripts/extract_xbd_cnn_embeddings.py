#!/usr/bin/env python3
"""Extract ResNet18 ROI embeddings for xBD buildings (pre/post/diff, 1536-d)."""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.extract_xbd_damage_features import (  # noqa: E402
    _bbox,
    _image_pairs,
    _label_path,
    _load_buildings,
)


def _roi_crops(pre_image: Image.Image, post_image: Image.Image, polygon) -> Optional[Tuple[Image.Image, Image.Image]]:
    width, height = pre_image.size
    box = _bbox(polygon, width, height)
    if box is None:
        return None
    left, top, right, bottom = box
    pre_crop = pre_image.crop((left, top, right + 1, bottom + 1)).convert("RGB")
    post_crop = post_image.crop((left, top, right + 1, bottom + 1)).convert("RGB")
    mask = Image.new("L", pre_crop.size, 0)
    from PIL import ImageDraw

    shifted = [(x - left, y - top) for x, y in polygon]
    ImageDraw.Draw(mask).polygon(shifted, fill=255)
    black = Image.new("RGB", pre_crop.size, (0, 0, 0))
    pre_crop = Image.composite(pre_crop, black, mask)
    post_crop = Image.composite(post_crop, black, mask)
    return pre_crop, post_crop


def _image_transform(image_size: int = 224):
    from torchvision import transforms

    return transforms.Compose(
        [
            transforms.Resize((image_size, image_size)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ]
    )


def _tensor_from_crop(crop: Image.Image, transform, diff_from=None):
    import torch

    tensor = transform(crop)
    if diff_from is None:
        return tensor
    diff = (tensor - diff_from).clamp(-1.0, 1.0)
    diff = ((diff + 1.0) / 2.0).clamp(0.0, 1.0)
    return diff


def _build_backbone(device: str):
    import torch
    import torch.nn as nn
    from torchvision.models import ResNet18_Weights, resnet18

    backbone = resnet18(weights=ResNet18_Weights.DEFAULT)
    backbone.fc = nn.Identity()
    backbone.eval()
    backbone.to(device)
    return backbone


def extract_embeddings(
    input_root: Path,
    output_npz: Path,
    batch_size: int = 32,
    limit: int = 0,
    device: str = "cpu",
) -> dict:
    import torch

    images_dir = input_root / "images"
    labels_dir = input_root / "labels"
    pairs = _image_pairs(images_dir)
    backbone = _build_backbone(device)

    keys: List[str] = []
    vectors: List[np.ndarray] = []
    pending_pre: List = []
    pending_post: List = []
    pending_keys: List[str] = []
    skipped = 0
    processed_pairs = 0
    transform = _image_transform()

    def flush_batch() -> None:
        nonlocal pending_pre, pending_post, pending_keys
        if not pending_keys:
            return
        import torch

        pre_batch = torch.stack(pending_pre, dim=0).to(device)
        post_batch = torch.stack(pending_post, dim=0).to(device)
        diff_batch = ((post_batch - pre_batch).clamp(-1.0, 1.0) + 1.0) / 2.0
        with torch.no_grad():
            pre_emb = backbone(pre_batch).detach().cpu().numpy().astype(np.float32)
            post_emb = backbone(post_batch).detach().cpu().numpy().astype(np.float32)
            diff_emb = backbone(diff_batch).detach().cpu().numpy().astype(np.float32)
        merged = np.concatenate([pre_emb, post_emb, diff_emb], axis=1)
        for key, row in zip(pending_keys, merged):
            keys.append(key)
            vectors.append(row)
        pending_pre = []
        pending_post = []
        pending_keys = []

    for sample_index, (sample_id, pair) in enumerate(sorted(pairs.items())):
        pre_path = pair.get("pre")
        post_path = pair.get("post")
        post_label = _label_path(labels_dir, sample_id, "post")
        if not pre_path or not post_path or not post_label.exists():
            skipped += 1
            continue
        pre_image = Image.open(pre_path).convert("RGB")
        post_image = Image.open(post_path).convert("RGB")
        for building_index, building in enumerate(_load_buildings(post_label)):
            subtype = str(building["properties"].get("subtype") or "").strip().lower()
            if subtype in {"un-classified", "unclassified"}:
                skipped += 1
                continue
            crops = _roi_crops(pre_image, post_image, building["polygon"])
            if crops is None:
                skipped += 1
                continue
            pre_crop, post_crop = crops
            pending_pre.append(transform(pre_crop))
            pending_post.append(transform(post_crop))
            pending_keys.append(f"{sample_id}:{building_index}")
            if limit and len(pending_keys) + len(keys) >= limit:
                flush_batch()
                output_npz.parent.mkdir(parents=True, exist_ok=True)
                matrix = np.stack(vectors, axis=0)
                np.savez_compressed(output_npz, keys=np.array(keys, dtype=object), embeddings=matrix)
                return {
                    "input_root": str(input_root),
                    "output_npz": str(output_npz),
                    "embedding_dim": int(matrix.shape[1]) if len(matrix) else 512,
                    "rows": len(keys),
                    "skipped_items": skipped,
                    "limited_to": limit,
                }
            if len(pending_keys) >= batch_size:
                flush_batch()
        processed_pairs += 1
        if sample_index > 0 and sample_index % 50 == 0:
            print(f"processed {sample_index} image pairs, embeddings={len(keys)}", flush=True)

    flush_batch()
    if not vectors:
        raise RuntimeError("no CNN embeddings extracted")
    matrix = np.stack(vectors, axis=0)
    output_npz.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output_npz, keys=np.array(keys, dtype=object), embeddings=matrix)
    return {
        "input_root": str(input_root),
        "output_npz": str(output_npz),
        "embedding_dim": int(matrix.shape[1]),
        "rows": len(keys),
        "sample_pairs": processed_pairs,
        "skipped_items": skipped,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract ResNet18 embeddings for xBD building ROIs.")
    parser.add_argument("--input-root", default="data/xbd/train/train")
    parser.add_argument("--output-npz", default="data/xbd/processed/xbd_cnn_embeddings_train.npz")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--limit", type=int, default=0, help="Stop after N building rows (0 = all).")
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    report = extract_embeddings(
        Path(args.input_root),
        Path(args.output_npz),
        batch_size=max(1, int(args.batch_size)),
        limit=max(0, int(args.limit)),
        device=str(args.device),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

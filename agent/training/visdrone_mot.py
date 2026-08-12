"""VisDrone2019-MOT 数据集加载，供 MOTR+Kalman 训练。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torch.utils.data import Dataset


@dataclass(frozen=True)
class TrackBox:
    frame_idx: int
    track_id: int
    x: float
    y: float
    w: float
    h: float


def parse_visdrone_mot_line(line: str) -> TrackBox | None:
    parts = line.strip().split(",")
    if len(parts) < 6:
        return None
    frame_idx = int(parts[0])
    track_id = int(parts[1])
    if track_id <= 0:
        return None
    x, y, w, h = map(float, parts[2:6])
    if w <= 1 or h <= 1:
        return None
    return TrackBox(frame_idx, track_id, x, y, w, h)


def load_sequence_annotations(ann_path: Path) -> dict[int, list[TrackBox]]:
    by_frame: dict[int, list[TrackBox]] = {}
    text = ann_path.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        if not line.strip():
            continue
        box = parse_visdrone_mot_line(line)
        if box is None:
            continue
        by_frame.setdefault(box.frame_idx, []).append(box)
    return by_frame


def discover_sequences(root: Path) -> list[tuple[str, Path, Path]]:
    """返回 (seq_name, sequences_dir, annotation_file)。"""
    ann_dir = root / "annotations"
    seq_root = root / "sequences"
    if not ann_dir.is_dir() or not seq_root.is_dir():
        raise FileNotFoundError(f"期望 {root}/annotations 与 {root}/sequences")
    out: list[tuple[str, Path, Path]] = []
    for ann in sorted(ann_dir.glob("*.txt")):
        name = ann.stem
        seq_dir = seq_root / name
        if seq_dir.is_dir():
            out.append((name, seq_dir, ann))
    return out


def frame_image_path(seq_dir: Path, frame_idx: int) -> Path:
    return seq_dir / f"{frame_idx:07d}.jpg"


def bbox_xywh_to_xyxy(box: TrackBox) -> tuple[float, float, float, float]:
    return box.x, box.y, box.x + box.w, box.y + box.h


def bbox_to_state_xywh(box: TrackBox) -> list[float]:
    cx = box.x + box.w / 2
    cy = box.y + box.h / 2
    return [cx, cy, box.w, box.h, 0.0, 0.0]


def state_with_velocity(prev: TrackBox, curr: TrackBox) -> list[float]:
    pcx = prev.x + prev.w / 2
    pcy = prev.y + prev.h / 2
    ccx = curr.x + curr.w / 2
    ccy = curr.y + curr.h / 2
    return [pcx, pcy, prev.w, prev.h, ccx - pcx, ccy - pcy]


_IMAGENET_MEAN = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
_IMAGENET_STD = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)


def crop_tensor_from_image(img: Image.Image, xyxy: tuple[float, float, float, float], crop_size: int) -> torch.Tensor | None:
    x1, y1, x2, y2 = xyxy
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(img.size[0], int(x2)), min(img.size[1], int(y2))
    if x2 <= x1 or y2 <= y1:
        return None
    arr = np.array(img.crop((x1, y1, x2, y2)).resize((crop_size, crop_size)), dtype=np.float32) / 255.0
    t = torch.from_numpy(arr).permute(2, 0, 1)
    return (t - _IMAGENET_MEAN) / _IMAGENET_STD


class VisDroneAssociationDataset(Dataset):
    """正样本：同一 track 相邻帧 crop；负样本：同帧不同 track 或随机错配。"""

    def __init__(
        self,
        root: Path,
        *,
        crop_size: int = 96,
        frame_gap: int = 1,
        samples_per_epoch: int = 8000,
        rng_seed: int = 42,
    ):
        self.root = root
        self.crop_size = crop_size
        self.frame_gap = frame_gap
        self.samples_per_epoch = samples_per_epoch
        self.rng = np.random.default_rng(rng_seed)
        self.sequences = discover_sequences(root)
        self._ann_cache: dict[str, dict[int, list[TrackBox]]] = {}
        self.positive_pairs: list[tuple[str, int, int, int]] = []
        print(f"[VisDrone] indexing {len(self.sequences)} sequences ...", flush=True)
        self._index_pairs()
        print(f"[VisDrone] positive pairs: {len(self.positive_pairs)}", flush=True)

    def _index_pairs(self) -> None:
        for seq_name, seq_dir, ann_path in self.sequences:
            by_frame = load_sequence_annotations(ann_path)
            frame_ids = sorted(by_frame.keys())
            id_map: dict[int, dict[int, TrackBox]] = {}
            for fid in frame_ids:
                for box in by_frame[fid]:
                    id_map.setdefault(box.track_id, {})[fid] = box
            for tid, frames in id_map.items():
                fids = sorted(frames.keys())
                for i in range(len(fids) - self.frame_gap):
                    f0, f1 = fids[i], fids[i + self.frame_gap]
                    if f1 - f0 != self.frame_gap:
                        continue
                    p0 = frame_image_path(seq_dir, f0)
                    p1 = frame_image_path(seq_dir, f1)
                    if p0.is_file() and p1.is_file():
                        self.positive_pairs.append((seq_name, tid, f0, f1))
            # 释放大标注占用的内存
            del by_frame, id_map

    def __len__(self) -> int:
        return self.samples_per_epoch

    def _seq_lookup(self) -> dict[str, tuple[Path, Path]]:
        return {name: (seq_dir, ann) for name, seq_dir, ann in self.sequences}

    def _frames_for_seq(self, seq_name: str) -> dict[int, list[TrackBox]]:
        if seq_name not in self._ann_cache:
            lookup = self._seq_lookup()
            _, ann_path = lookup[seq_name]
            self._ann_cache[seq_name] = load_sequence_annotations(ann_path)
        return self._ann_cache[seq_name]

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        seq_name, tid, f0, f1 = self.positive_pairs[int(self.rng.integers(0, len(self.positive_pairs)))]
        lookup = self._seq_lookup()
        seq_dir, _ = lookup[seq_name]
        by_frame = self._frames_for_seq(seq_name)

        box0 = next(b for b in by_frame[f0] if b.track_id == tid)
        box1 = next(b for b in by_frame[f1] if b.track_id == tid)

        img0 = Image.open(frame_image_path(seq_dir, f0)).convert("RGB")
        img1 = Image.open(frame_image_path(seq_dir, f1)).convert("RGB")
        t0 = crop_tensor_from_image(img0, bbox_xywh_to_xyxy(box0), self.crop_size)
        t1 = crop_tensor_from_image(img1, bbox_xywh_to_xyxy(box1), self.crop_size)
        if t0 is None or t1 is None:
            return self.__getitem__((idx + 1) % len(self))

        neg_candidates = [b for b in by_frame[f1] if b.track_id != tid]
        if neg_candidates:
            neg = neg_candidates[int(self.rng.integers(0, len(neg_candidates)))]
            t_neg = crop_tensor_from_image(img1, bbox_xywh_to_xyxy(neg), self.crop_size)
        else:
            t_neg = t1 + torch.randn_like(t1) * 0.05

        if t_neg is None:
            t_neg = t1 + torch.randn_like(t1) * 0.05

        return {
            "anchor": t0,
            "positive": t1,
            "negative": t_neg,
            "state": torch.tensor(state_with_velocity(box0, box1), dtype=torch.float32),
            "obs": torch.tensor(bbox_to_state_xywh(box1)[:4], dtype=torch.float32),
        }

"""MOTR 关联网络 + Neural Kalman Filter (KalmanNet 风格 GRU)。"""

from __future__ import annotations

from typing import Any

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.models as tvm


class KalmanNet(nn.Module):
    """神经卡尔曼：GRU 预测卡尔曼增益并更新 bbox 状态 [cx, cy, w, h, vx, vy]。"""

    def __init__(self, state_dim: int = 6, obs_dim: int = 4, hidden: int = 64):
        super().__init__()
        self.gru = nn.GRU(obs_dim + state_dim, hidden, batch_first=True)
        self.gain_head = nn.Linear(hidden, state_dim)
        self.state_dim = state_dim

    def forward(
        self, state: torch.Tensor, observation: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor]:
        x = torch.cat([state, observation], dim=-1).unsqueeze(1)
        h, _ = self.gru(x)
        gain = torch.sigmoid(self.gain_head(h[:, -1, :]))
        predicted = state.clone()
        predicted[:, 0] = state[:, 0] + state[:, 4]
        predicted[:, 1] = state[:, 1] + state[:, 5]
        predicted[:, 2:4] = state[:, 2:4]
        updated = state.clone()
        updated[:, 0:4] = gain[:, 0:4] * observation + (1 - gain[:, 0:4]) * predicted[:, 0:4]
        updated[:, 4] = gain[:, 4] * (observation[:, 0] - state[:, 0]) + (1 - gain[:, 4]) * state[:, 4]
        updated[:, 5] = gain[:, 5] * (observation[:, 1] - state[:, 1]) + (1 - gain[:, 5]) * state[:, 5]
        return updated, gain.mean(dim=1)


class MOTRCostNet(nn.Module):
    """MOTR 风格关联代价：ResNet18 提取 crop 特征 + Transformer 编码匹配。"""

    def __init__(self, embed_dim: int = 128):
        super().__init__()
        backbone = tvm.resnet18(weights=tvm.ResNet18_Weights.IMAGENET1K_V1)
        self.backbone = nn.Sequential(*list(backbone.children())[:-1])
        self.proj = nn.Linear(512, embed_dim)
        self.encoder = nn.TransformerEncoder(
            nn.TransformerEncoderLayer(d_model=embed_dim, nhead=4, batch_first=True),
            num_layers=2,
        )

    def embed_crops(self, crops: torch.Tensor) -> torch.Tensor:
        feat = self.backbone(crops).flatten(1)
        return F.normalize(self.proj(feat), dim=-1)

    def association_cost(self, det_emb: torch.Tensor, track_emb: torch.Tensor) -> torch.Tensor:
        if track_emb.numel() == 0:
            return torch.zeros(det_emb.size(0), 0, device=det_emb.device)
        tokens = torch.cat([det_emb.unsqueeze(1), track_emb.unsqueeze(0).expand(det_emb.size(0), -1, -1)], dim=1)
        encoded = self.encoder(tokens)
        return 1.0 - F.cosine_similarity(encoded[:, 0:1, :], encoded[:, 1:, :], dim=-1).squeeze(1)


def _bbox_to_state(bbox: list[float]) -> list[float]:
    x1, y1, x2, y2 = bbox
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    w, h = max(1.0, x2 - x1), max(1.0, y2 - y1)
    return [cx, cy, w, h, 0.0, 0.0]


def _state_to_bbox(state: list[float]) -> list[float]:
    cx, cy, w, h = state[0], state[1], state[2], state[3]
    return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]


class MOTRTracker(nn.Module):
    def __init__(self):
        super().__init__()
        self.cost_net = MOTRCostNet()
        self.kalman = KalmanNet()

    @torch.inference_mode()
    def track(
        self,
        verified: list[dict[str, Any]],
        prior_tracks: list[dict[str, Any]],
        image_rgb,
        *,
        device: str,
        iou_thr: float,
        base_lat: float,
        base_lon: float,
    ) -> dict[str, Any]:
        from PIL import Image

        self.to(device)
        img = Image.fromarray(image_rgb) if image_rgb is not None else None
        crop_size = 96

        def crop_tensor(bbox: list[float]) -> torch.Tensor | None:
            if img is None:
                return None
            x1, y1, x2, y2 = [int(v) for v in bbox]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(img.size[0], x2), min(img.size[1], y2)
            if x2 <= x1 or y2 <= y1:
                return None
            arr = np.array(img.crop((x1, y1, x2, y2)).resize((crop_size, crop_size)), dtype=np.float32) / 255.0
            t = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0).to(device)
            mean = torch.tensor([0.485, 0.456, 0.406], device=device).view(1, 3, 1, 1)
            std = torch.tensor([0.229, 0.224, 0.225], device=device).view(1, 3, 1, 1)
            return (t - mean) / std

        track_states: dict[str, dict] = {}
        for pt in prior_tracks:
            tid = pt.get("track_id", "")
            bbox = pt.get("bbox") or pt.get("last_bbox") or [0, 0, 0, 0]
            track_states[tid] = {"bbox": bbox, "state": _bbox_to_state(bbox), "conf": float(pt.get("confidence", 0.5))}

        next_idx = len(prior_tracks) + 1
        for t in prior_tracks:
            tid = t.get("track_id", "")
            if tid.startswith("T-"):
                try:
                    next_idx = max(next_idx, int(tid.split("-")[1]) + 1)
                except ValueError:
                    pass

        prior_ids = list(track_states.keys())
        valid_prior_ids: list[str] = []
        prior_crop_tensors: list[torch.Tensor] = []
        for tid in prior_ids:
            crop = crop_tensor(track_states[tid]["bbox"])
            if crop is not None:
                valid_prior_ids.append(tid)
                prior_crop_tensors.append(crop)

        track_emb = (
            self.cost_net.embed_crops(torch.cat(prior_crop_tensors, dim=0))
            if prior_crop_tensors
            else torch.zeros(0, 128, device=device)
        )

        tracks_out: list[dict] = []
        used: set[str] = set()

        for det in verified:
            bbox = det.get("bbox") or [0, 0, 0, 0]
            conf = float(det.get("confidence", 0.5))
            det_crop = crop_tensor(bbox)
            if det_crop is not None and track_emb.numel() > 0:
                det_emb = self.cost_net.embed_crops(det_crop)
                cost = self.cost_net.association_cost(det_emb, track_emb)[0]
                best_j = int(cost.argmin().item()) if cost.numel() else -1
                best_cost = float(cost[best_j].item()) if best_j >= 0 else 1.0
                matched_tid = (
                    valid_prior_ids[best_j] if best_j >= 0 and best_cost < (1.0 - iou_thr) else ""
                )
            else:
                matched_tid = ""

            obs = torch.tensor(_bbox_to_state(bbox)[:4], dtype=torch.float32, device=device).unsqueeze(0)
            if matched_tid and matched_tid not in used:
                tid = matched_tid
                used.add(tid)
                state_t = torch.tensor(track_states[tid]["state"], dtype=torch.float32, device=device).unsqueeze(0)
                updated, gain = self.kalman(state_t, obs)
                state_list = updated[0].cpu().tolist()
                bbox = _state_to_bbox(state_list)
                track_states[tid]["state"] = state_list
                track_states[tid]["bbox"] = bbox
                kalman_gain = float(gain.item())
            else:
                tid = f"T-{next_idx:04d}"
                next_idx += 1
                state_list = _bbox_to_state(bbox)
                track_states[tid] = {"bbox": bbox, "state": state_list, "conf": conf}
                kalman_gain = 0.5

            cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
            idx = len(tracks_out)
            tracks_out.append(
                {
                    "track_id": tid,
                    "class_name": det.get("class_name", "unknown"),
                    "confidence": conf,
                    "state": "active",
                    "bbox": bbox,
                    "last_bbox": bbox,
                    "position_px": [cx, cy],
                    "geo": {"lat": base_lat + idx * 0.001, "lon": base_lon + idx * 0.001, "alt_m": 120.0},
                    "kalman_gain": round(kalman_gain, 3),
                }
            )

        return {"tracks": tracks_out, "associations": len(tracks_out)}

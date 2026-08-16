"""Multimodal Mamba：选择性状态空间序列融合（PyTorch 实现）。"""

from __future__ import annotations

from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F


class MultimodalMambaBlock(nn.Module):
    """Mamba 风格 SSM 块：depthwise conv + 门控状态混合。"""

    def __init__(self, d_model: int, d_state: int = 16, expand: int = 2):
        super().__init__()
        self.d_model = d_model
        self.d_inner = d_model * expand
        self.in_proj = nn.Linear(d_model, self.d_inner * 2, bias=False)
        self.conv1d = nn.Conv1d(
            self.d_inner, self.d_inner, kernel_size=3, padding=1, groups=self.d_inner
        )
        self.ssm_proj = nn.Linear(self.d_inner, d_state)
        self.ssm_back = nn.Linear(d_state, self.d_inner)
        self.out_proj = nn.Linear(self.d_inner, d_model, bias=False)
        self.norm = nn.LayerNorm(d_model)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, L, D)
        residual = x
        x = self.norm(x)
        xz = self.in_proj(x)
        x_inner, z = xz.chunk(2, dim=-1)
        x_inner = self.conv1d(x_inner.transpose(1, 2)).transpose(1, 2)
        x_inner = F.silu(x_inner)
        state = self.ssm_back(F.silu(self.ssm_proj(x_inner)))
        y = x_inner * F.silu(z) + state
        return residual + self.out_proj(y)

    def fused_tensor(
        self,
        sequence: torch.Tensor,
        mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """Fuse a padded modality sequence into one normalized representation."""
        fused_sequence = self.forward(sequence)
        if mask is None:
            pooled = fused_sequence.mean(dim=1)
        else:
            weights = mask.to(dtype=fused_sequence.dtype).unsqueeze(-1)
            pooled = (fused_sequence * weights).sum(dim=1) / weights.sum(dim=1).clamp_min(1.0)
        return F.normalize(pooled, dim=-1)

    def _prepare_vector(self, value: list[float], device: str) -> torch.Tensor:
        vector = torch.tensor(value, dtype=torch.float32, device=device).view(-1)
        if vector.numel() < self.d_model:
            vector = F.pad(vector, (0, self.d_model - vector.numel()))
        elif vector.numel() > self.d_model:
            vector = vector[: self.d_model]
        return vector

    @torch.inference_mode()
    def fuse(
        self,
        embeddings: dict[str, list[float]],
        tracks: list[dict],
        *,
        device: str,
    ) -> dict[str, list[float]]:
        if not embeddings:
            return {}
        keys = list(embeddings)
        vecs = [self._prepare_vector(embeddings[key], device) for key in keys]
        seq = torch.stack(vecs, dim=0).unsqueeze(0)  # (1, L, D)
        self.to(device)
        fused_seq = self.forward(seq)[0]
        global_vec = F.normalize(fused_seq.mean(dim=0), dim=0)
        key_to_index = {key: index for index, key in enumerate(keys)}

        fused: dict[str, list[float]] = {}
        for track in tracks:
            tid = track.get("track_id", "unknown")
            sensor_id = track.get("sensor_id")
            if sensor_id in key_to_index:
                local_vec = fused_seq[key_to_index[sensor_id]]
                vec = F.normalize(0.6 * local_vec + 0.4 * global_vec, dim=0)
            else:
                vec = global_vec
            fused[tid] = vec.cpu().tolist()

        if not tracks:
            fused["GLOBAL"] = global_vec.cpu().tolist()
        return fused

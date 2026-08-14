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
        vecs = [
            torch.tensor(v, dtype=torch.float32, device=device).view(-1)
            for v in embeddings.values()
        ]
        seq = torch.stack(vecs, dim=0).unsqueeze(0)  # (1, L, D)
        d_model = self.d_model
        if seq.size(-1) != d_model:
            d = seq.size(-1)
            if d < d_model:
                seq = F.pad(seq, (0, d_model - d))
            else:
                seq = seq[..., :d_model]
        self.to(device)
        fused_seq = self.forward(seq)[0]
        global_vec = fused_seq.mean(dim=0)

        fused: dict[str, list[float]] = {}
        for i, track in enumerate(tracks):
            tid = track.get("track_id", "unknown")
            idx = min(i, fused_seq.size(0) - 1)
            vec = 0.6 * fused_seq[idx] + 0.4 * global_vec
            fused[tid] = vec.cpu().tolist()

        if not tracks:
            fused["GLOBAL"] = global_vec.cpu().tolist()
        return fused

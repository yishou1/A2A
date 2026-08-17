"""ODConv (Omni-Dimensional Dynamic Convolution) 检测置信度精炼网络。"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class ODConv2d(nn.Module):
    """全维动态卷积（ODConv, ICLR 2022）精简实现。"""

    def __init__(self, in_planes: int, out_planes: int, kernel_size: int = 3, stride: int = 1, padding: int = 1):
        super().__init__()
        self.in_planes = in_planes
        self.out_planes = out_planes
        self.kernel_size = kernel_size
        self.stride = stride
        self.padding = padding
        self.weight = nn.Parameter(torch.randn(out_planes, in_planes, kernel_size, kernel_size) * 0.02)
        self.attention = nn.Sequential(
            nn.AdaptiveAvgPool2d(1),
            nn.Conv2d(in_planes, max(in_planes // 4, 4), 1),
            nn.ReLU(inplace=True),
            nn.Conv2d(max(in_planes // 4, 4), in_planes, 1),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # 输入通道动态重标定 + 标准卷积（ODConv 常用实现方式）
        att = self.attention(x)
        x = x * att
        return F.conv2d(x, self.weight, stride=self.stride, padding=self.padding)


class ODConvRefiner(nn.Module):
    """对检测 crop 做 ODConv 特征提取并输出精炼置信度。"""

    def __init__(self, embed_dim: int = 64):
        super().__init__()
        self.stem = nn.Sequential(
            nn.Conv2d(3, 32, 3, stride=2, padding=1),
            nn.BatchNorm2d(32),
            nn.ReLU(inplace=True),
            ODConv2d(32, 64, 3, stride=2, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True),
            nn.AdaptiveAvgPool2d(1),
        )
        self.head = nn.Sequential(
            nn.Linear(64 + 1, embed_dim),
            nn.ReLU(inplace=True),
            nn.Linear(embed_dim, 1),
            nn.Sigmoid(),
        )

    def forward(self, crops: torch.Tensor, base_conf: torch.Tensor) -> torch.Tensor:
        """
        crops: (N, 3, H, W) in [0,1]
        base_conf: (N, 1) RT-DETR 原始置信度
        returns refined confidence (N,)
        """
        feat = self.stem(crops).flatten(1)
        x = torch.cat([feat, base_conf], dim=1)
        delta = self.head(x).squeeze(1)
        return torch.clamp(0.5 * base_conf.squeeze(1) + 0.5 * delta, 0.05, 0.99)

    @torch.inference_mode()
    def refine_detections(
        self,
        image_rgb,
        detections: list[dict],
        *,
        device: str,
        crop_size: int = 128,
    ) -> list[dict]:
        if not detections:
            return detections
        import numpy as np
        from PIL import Image

        img = Image.fromarray(image_rgb)
        crops, confs, valid_idx = [], [], []
        w, h = img.size
        for i, det in enumerate(detections):
            bbox = det.get("bbox") or [0, 0, 0, 0]
            x1, y1, x2, y2 = bbox
            x1, y1 = max(0, int(x1)), max(0, int(y1))
            x2, y2 = min(w, int(x2)), min(h, int(y2))
            if x2 <= x1 or y2 <= y1:
                continue
            crop = img.crop((x1, y1, x2, y2)).resize((crop_size, crop_size))
            arr = np.array(crop, dtype=np.float32) / 255.0
            crops.append(torch.from_numpy(arr).permute(2, 0, 1))
            confs.append(float(det.get("confidence", 0.5)))
            valid_idx.append(i)

        if not crops:
            return detections

        batch_crops = torch.stack(crops).to(device)
        batch_conf = torch.tensor(confs, dtype=torch.float32, device=device).unsqueeze(1)
        refined = self.forward(batch_crops, batch_conf).cpu().tolist()

        out = [dict(d) for d in detections]
        for j, idx in enumerate(valid_idx):
            out[idx]["confidence"] = round(refined[j], 4)
            out[idx]["odconv_refined"] = True
        return out

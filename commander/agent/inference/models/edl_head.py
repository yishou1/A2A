"""EDL (Evidential Deep Learning) 检测验证头。"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class EvidentialHead(nn.Module):
    """Dirichlet 证据头：输出类别证据并计算认知/偶然不确定性。"""

    def __init__(self, in_dim: int = 6, num_classes: int = 2):
        super().__init__()
        self.num_classes = num_classes
        self.net = nn.Sequential(
            nn.Linear(in_dim, 32),
            nn.ReLU(inplace=True),
            nn.Linear(32, num_classes),
        )
        self._init_detection_prior()

    def _init_detection_prior(self) -> None:
        """校准初始化：检测置信度越高 → verified 类证据越强。"""
        with torch.no_grad():
            self.net[0].weight.zero_()
            self.net[0].bias.zero_()
            self.net[0].weight[:, 0] = 3.0

            self.net[2].weight.zero_()
            self.net[2].bias.zero_()
            self.net[2].weight[1, :] = 1.2
            self.net[2].weight[0, :] = -0.8

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        logits = self.net(x)
        evidence = F.softplus(logits)
        alpha = evidence + 1.0
        s = alpha.sum(dim=1, keepdim=True)
        prob = alpha / s
        epistemic = self.num_classes / s.squeeze(1)
        aleatoric = -torch.sum(prob * (torch.digamma(alpha + 1) - torch.digamma(s + 1)), dim=1)
        return prob, epistemic, aleatoric

    @torch.inference_mode()
    def verify(
        self,
        detections: list[dict],
        *,
        device: str,
        min_conf: float,
        max_epistemic: float,
    ) -> list[dict]:
        if not detections:
            return []
        feats = []
        for det in detections:
            bbox = det.get("bbox") or [0, 0, 0, 0]
            w = max(1.0, bbox[2] - bbox[0])
            h = max(1.0, bbox[3] - bbox[1])
            feats.append(
                [
                    float(det.get("confidence", 0.5)),
                    w / 640.0,
                    h / 640.0,
                    (bbox[0] + bbox[2]) / 2 / 640.0,
                    (bbox[1] + bbox[3]) / 2 / 640.0,
                    float(det.get("damage_score", 0.0) or 0.0),
                ]
            )
        x = torch.tensor(feats, dtype=torch.float32, device=device)
        prob, epistemic, aleatoric = self.forward(x)
        verified: list[dict] = []
        for i, det in enumerate(detections):
            conf = float(prob[i, 1 if prob.shape[1] > 1 else 0].item())
            epi = float(epistemic[i].item())
            ale = float(aleatoric[i].item())
            if conf >= min_conf and epi <= max_epistemic:
                verified.append(
                    {
                        **det,
                        "verified": True,
                        "confidence": round(conf, 4),
                        "epistemic_uncertainty": round(epi, 4),
                        "aleatoric_uncertainty": round(ale, 4),
                        "edl_belief": round(conf, 4),
                    }
                )
        return verified

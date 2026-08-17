"""SupCon + Meta-Learning (Prototypical Network) 分类网络。"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class SupConMetaNet(nn.Module):
    """监督对比投影头 + 原型元学习分类。"""

    def __init__(self, in_dim: int = 512, proj_dim: int = 128, num_classes: int = 4):
        super().__init__()
        self.num_classes = num_classes
        self.labels = ("friendly", "neutral", "hostile", "unknown")
        self.encoder = nn.Sequential(
            nn.Linear(in_dim, 256),
            nn.ReLU(inplace=True),
            nn.Linear(256, proj_dim),
        )
        self.prototypes = nn.Parameter(torch.randn(num_classes, proj_dim) * 0.02)

    def project(self, x: torch.Tensor) -> torch.Tensor:
        return F.normalize(self.encoder(x), dim=-1)

    def meta_prototypes(self, support: list[dict], device: str) -> torch.Tensor:
        protos = self.prototypes.clone()
        if not support:
            return protos
        for shot in support:
            label = shot.get("label", "unknown")
            if label not in self.labels:
                continue
            idx = self.labels.index(label)
            emb = shot.get("embedding")
            if not emb:
                continue
            v = torch.tensor(emb, dtype=torch.float32, device=device)
            if v.numel() != protos.size(1):
                v = F.adaptive_avg_pool1d(v.unsqueeze(0).unsqueeze(0), protos.size(1)).squeeze()
            v = F.normalize(v, dim=0)
            protos[idx] = 0.7 * protos[idx] + 0.3 * v
        return F.normalize(protos, dim=-1)

    @torch.inference_mode()
    def classify(
        self,
        fused: dict[str, list[float]],
        *,
        device: str,
        support_shots: list[dict] | None = None,
        temperature: float = 0.07,
    ) -> list[dict]:
        if not fused:
            return []
        self.to(device)
        protos = self.meta_prototypes(support_shots or [], device)
        results: list[dict] = []
        for tid, vec in fused.items():
            x = torch.tensor(vec, dtype=torch.float32, device=device).unsqueeze(0)
            if x.size(-1) != self.encoder[0].in_features:
                x = F.adaptive_avg_pool1d(x.unsqueeze(1), self.encoder[0].in_features).squeeze(1)
            z = self.project(x)
            sims = (z @ protos.T)[0] / temperature
            prob = F.softmax(sims, dim=0)
            idx = int(prob.argmax().item())
            label = self.labels[idx]
            results.append(
                {
                    "target_id": tid,
                    "label": label,
                    "confidence": round(float(prob[idx].item()), 4),
                    "support_shots": len(support_shots or []),
                    "similarity_scores": {
                        self.labels[i]: round(float(prob[i].item()), 4) for i in range(len(self.labels))
                    },
                }
            )
        return results

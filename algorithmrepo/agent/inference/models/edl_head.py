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

    def forward(
        self, x: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        logits = self.net(x)
        evidence = F.softplus(logits)
        alpha = evidence + 1.0
        s = alpha.sum(dim=1, keepdim=True)
        prob = alpha / s
        epistemic = self.num_classes / s.squeeze(1)
        aleatoric = -torch.sum(prob * (torch.digamma(alpha + 1) - torch.digamma(s + 1)), dim=1)
        return evidence, alpha, prob, epistemic, aleatoric

    @staticmethod
    def detection_features(det: dict) -> list[float]:
        bbox = det.get("bbox") or [0, 0, 0, 0]
        width = max(1.0, float(bbox[2]) - float(bbox[0]))
        height = max(1.0, float(bbox[3]) - float(bbox[1]))
        return [
            float(det.get("confidence", 0.5)),
            width / 640.0,
            height / 640.0,
            (float(bbox[0]) + float(bbox[2])) / 2.0 / 640.0,
            (float(bbox[1]) + float(bbox[3])) / 2.0 / 640.0,
            float(det.get("damage_score", 0.0) or 0.0),
        ]

    @torch.inference_mode()
    def assess(
        self,
        detections: list[dict],
        *,
        device: str,
        min_conf: float,
        max_epistemic: float,
        review_margin: float,
    ) -> list[dict]:
        if not detections:
            return []
        feats = [self.detection_features(det) for det in detections]
        x = torch.tensor(feats, dtype=torch.float32, device=device)
        evidence, alpha, prob, epistemic, aleatoric = self.forward(x)
        assessments: list[dict] = []
        for i, det in enumerate(detections):
            conf = float(prob[i, 1 if prob.shape[1] > 1 else 0].item())
            epi = float(epistemic[i].item())
            ale = float(aleatoric[i].item())
            uncertain = epi > max_epistemic or abs(conf - min_conf) <= review_margin
            if uncertain:
                decision = "manual_review"
            elif conf >= min_conf:
                decision = "verified"
            else:
                decision = "rejected"
            feature_names = (
                "detector_confidence",
                "bbox_width_normalized",
                "bbox_height_normalized",
                "bbox_center_x_normalized",
                "bbox_center_y_normalized",
                "damage_score",
            )
            reasons = [
                f"verified_probability={conf:.4f} {'>=' if conf >= min_conf else '<'} threshold={min_conf:.4f}",
                f"epistemic_uncertainty={epi:.4f} {'<=' if epi <= max_epistemic else '>'} limit={max_epistemic:.4f}",
            ]
            assessments.append(
                {
                    **det,
                    "detector_confidence": round(float(det.get("confidence", 0.5)), 4),
                    "confidence": round(conf, 4),
                    "verified": decision == "verified",
                    "decision": decision,
                    "review_required": decision == "manual_review",
                    "epistemic_uncertainty": round(epi, 4),
                    "aleatoric_uncertainty": round(ale, 4),
                    "edl_belief": round(conf, 4),
                    "evidence": {
                        "features": {
                            name: round(float(value), 6)
                            for name, value in zip(feature_names, feats[i])
                        },
                        "class_probabilities": {
                            "rejected": round(float(prob[i, 0].item()), 6),
                            "verified": round(float(prob[i, 1].item()), 6),
                        },
                        "class_evidence": {
                            "rejected": round(float(evidence[i, 0].item()), 6),
                            "verified": round(float(evidence[i, 1].item()), 6),
                        },
                        "dirichlet_alpha": [round(float(value), 6) for value in alpha[i].tolist()],
                        "dirichlet_strength": round(float(alpha[i].sum().item()), 6),
                        "decision_reasons": reasons,
                    },
                }
            )
        return assessments

    @torch.inference_mode()
    def verify(
        self,
        detections: list[dict],
        *,
        device: str,
        min_conf: float,
        max_epistemic: float,
        review_margin: float = 0.08,
    ) -> list[dict]:
        return [
            item
            for item in self.assess(
                detections,
                device=device,
                min_conf=min_conf,
                max_epistemic=max_epistemic,
                review_margin=review_margin,
            )
            if item["verified"]
        ]

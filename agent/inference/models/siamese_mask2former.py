"""Siamese Mask2Former：共享 Mask2Former 权重的孪生毁伤分割。"""

from __future__ import annotations

from typing import Any

import numpy as np
import torch


class SiameseMask2Former(torch.nn.Module):
    """孪生 Mask2Former：对参考帧/当前帧分割掩码做差分得到毁伤区域。"""

    def __init__(self, model_id: str = "facebook/mask2former-swin-tiny-ade-semantic"):
        super().__init__()
        from transformers import Mask2FormerForUniversalSegmentation, Mask2FormerImageProcessor

        self.processor = Mask2FormerImageProcessor.from_pretrained(model_id)
        self.model = Mask2FormerForUniversalSegmentation.from_pretrained(model_id)
        self.model.eval()

    @property
    def device(self) -> torch.device:
        return next(self.model.parameters()).device

    def to_device(self, device: str) -> SiameseMask2Former:
        self.model.to(device)
        return self

    @torch.inference_mode()
    def _segment_mask(self, image_rgb: np.ndarray) -> np.ndarray:
        from PIL import Image

        pil = Image.fromarray(image_rgb)
        h, w = image_rgb.shape[:2]
        inputs = self.processor(images=pil, return_tensors="pt")
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        outputs = self.model(**inputs)
        seg = self.processor.post_process_semantic_segmentation(
            outputs, target_sizes=[(h, w)]
        )[0]
        return (seg.cpu().numpy() > 0).astype(np.uint8)

    @torch.inference_mode()
    def assess_pair(
        self,
        reference_rgb: np.ndarray,
        current_rgb: np.ndarray,
        *,
        sensor_id: str,
        gain: float = 4.0,
    ) -> dict[str, Any]:
        ref_mask = self._segment_mask(reference_rgb)
        cur_mask = self._segment_mask(current_rgb)
        if ref_mask.shape != cur_mask.shape:
            from PIL import Image

            cur_mask = np.array(
                Image.fromarray(cur_mask).resize((ref_mask.shape[1], ref_mask.shape[0]), Image.NEAREST)
            )
        damage_mask = np.logical_xor(ref_mask, cur_mask).astype(np.uint8)
        change_ratio = float(damage_mask.sum()) / max(damage_mask.size, 1)
        damage_score = round(min(1.0, change_ratio * gain), 4)
        return {
            "sensor_id": sensor_id,
            "damage_score": damage_score,
            "change_ratio": round(change_ratio, 4),
            "damage_mask_ref": "siamese_mask2former",
            "mask_pixels": int(damage_mask.sum()),
        }

"""ImageBind 跨模态统一表征；未安装 ImageBind 时自动回退 CLIP。"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import torch
from PIL import Image
from torchvision import transforms

from agent.inference.utils import decode_image_from_frame, frame_to_text

_VISION_TRANSFORM = transforms.Compose(
    [
        transforms.Resize(224, interpolation=transforms.InterpolationMode.BICUBIC),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(
            mean=(0.48145466, 0.4578275, 0.40821073),
            std=(0.26862954, 0.26130258, 0.27577711),
        ),
    ]
)


def _normalize(vec: np.ndarray) -> list[float]:
    flat = np.asarray(vec, dtype=np.float32).reshape(-1)
    return (flat / (np.linalg.norm(flat) + 1e-8)).tolist()


CLIP_DIM = 512


def _to_vector(feat) -> np.ndarray:
    """CLIP 输出可能是 Tensor 或 ModelOutput，统一为 1D 向量。"""
    if isinstance(feat, torch.Tensor):
        t = feat
    elif hasattr(feat, "pooler_output") and feat.pooler_output is not None:
        t = feat.pooler_output
    elif hasattr(feat, "last_hidden_state"):
        t = feat.last_hidden_state.mean(dim=1)
    else:
        raise TypeError(f"unsupported feature type: {type(feat)!r}")

    t = t.detach().cpu()
    if t.dim() == 1:
        return t.numpy()
    if t.dim() == 2:
        return t[0].numpy()
    if t.dim() >= 3:
        return t[0].mean(dim=0).numpy()
    raise ValueError(f"unexpected feature shape: {tuple(t.shape)}")


class _ClipEmbedder:
    """transformers CLIP 回退（无法从 GitHub 安装 ImageBind 时使用）。"""

    def __init__(self, device: str, *, clip_model_id: str = "openai/clip-vit-base-patch32"):
        from pathlib import Path

        from transformers import CLIPModel, CLIPProcessor

        from agent.inference.offline import is_offline_mode, resolve_model_ref

        path = resolve_model_ref(clip_model_id)
        local_only = is_offline_mode() or Path(path).is_dir()
        self.device = device
        self.processor = CLIPProcessor.from_pretrained(path, local_files_only=local_only)
        self.model = CLIPModel.from_pretrained(path, local_files_only=local_only)
        self.model.eval()
        self.model.to(device)

    @torch.inference_mode()
    def embed_frames(self, frames: list[dict[str, Any]]) -> dict[str, list[float]]:
        embeddings: dict[str, list[float]] = {}
        for frame in frames:
            sid = frame.get("sensor_id", "unknown")
            modality = frame.get("modality", "")

            if modality in ("eo_ir", "sar"):
                arr = decode_image_from_frame(frame)
                if arr is None:
                    continue
                inputs = self.processor(images=Image.fromarray(arr), return_tensors="pt")
                inputs = {k: v.to(self.device) for k, v in inputs.items()}
                vec = _to_vector(self.model.get_image_features(**inputs))
                embeddings[sid] = _normalize(vec)
                continue

            text = frame_to_text(frame)
            inputs = self.processor(
                text=[text], return_tensors="pt", padding=True, truncation=True
            )
            inputs = {k: v.to(self.device) for k, v in inputs.items()}
            vec = _to_vector(self.model.get_text_features(**inputs))
            embeddings[sid] = _normalize(vec)

        return embeddings


class ImageBindEmbedder:
    """封装 Meta ImageBind；不可用时使用 CLIP。"""

    def __init__(self, device: str, *, clip_model_id: str = "openai/clip-vit-base-patch32"):
        self.device = device
        self._backend = "imagebind"
        self._clip: _ClipEmbedder | None = None
        self.model = None
        self.ModalityType = None

        try:
            from imagebind.models import imagebind_model
            from imagebind.models.imagebind_model import ModalityType

            self.ModalityType = ModalityType
            self.model = imagebind_model.imagebind_huge(pretrained=True)
            self.model.eval()
            self.model.to(device)
        except ImportError:
            warnings.warn(
                "ImageBind 未安装，认知嵌入使用 CLIP 回退。"
                "离线环境请将 CLIP 打包到 models/pretrained/clip-vit-base-patch32。",
                stacklevel=2,
            )
            self._backend = "clip"
            self._clip = _ClipEmbedder(device, clip_model_id=clip_model_id)

    @torch.inference_mode()
    def embed_frames(self, frames: list[dict[str, Any]]) -> dict[str, list[float]]:
        if self._backend == "clip":
            assert self._clip is not None
            return self._clip.embed_frames(frames)

        from imagebind.data import load_and_transform_text

        embeddings: dict[str, list[float]] = {}
        ModalityType = self.ModalityType

        for frame in frames:
            sid = frame.get("sensor_id", "unknown")
            modality = frame.get("modality", "")

            if modality in ("eo_ir", "sar"):
                arr = decode_image_from_frame(frame)
                if arr is None:
                    continue
                pil = Image.fromarray(arr)
                tensor = _VISION_TRANSFORM(pil).unsqueeze(0).to(self.device)
                outputs = self.model({ModalityType.VISION: tensor})
                vec = outputs[ModalityType.VISION][0].cpu().numpy()
                embeddings[sid] = _normalize(vec)
                continue

            text = frame_to_text(frame)
            tokens = load_and_transform_text([text], self.device)
            outputs = self.model({ModalityType.TEXT: tokens})
            vec = outputs[ModalityType.TEXT][0].cpu().numpy()
            embeddings[sid] = _normalize(vec)

        return embeddings

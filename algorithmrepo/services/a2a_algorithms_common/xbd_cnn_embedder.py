"""Online ResNet18 ROI embeddings for xBD damage assessment."""
from __future__ import annotations

from typing import List, Optional, Tuple

from PIL import Image, ImageDraw

from .xbd_feature_extraction import bbox, normalize_polygon

CNN_EMBEDDING_DIM = 1536


def roi_crops(
    pre_image: Image.Image,
    post_image: Image.Image,
    polygon,
) -> Optional[Tuple[Image.Image, Image.Image]]:
    points = normalize_polygon(polygon)
    if not points:
        return None
    width, height = pre_image.size
    box = bbox(points, width, height)
    if box is None:
        return None
    left, top, right, bottom = box
    pre_crop = pre_image.crop((left, top, right + 1, bottom + 1)).convert("RGB")
    post_crop = post_image.crop((left, top, right + 1, bottom + 1)).convert("RGB")
    mask = Image.new("L", pre_crop.size, 0)
    shifted = [(x - left, y - top) for x, y in points]
    ImageDraw.Draw(mask).polygon(shifted, fill=255)
    black = Image.new("RGB", pre_crop.size, (0, 0, 0))
    pre_crop = Image.composite(pre_crop, black, mask)
    post_crop = Image.composite(post_crop, black, mask)
    return pre_crop, post_crop


def image_transform(image_size: int = 224):
    from torchvision import transforms

    return transforms.Compose(
        [
            transforms.Resize((image_size, image_size)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ]
    )


_backbone = None
_backbone_device = None


def _build_backbone(device: str = "cpu"):
    import torch
    import torch.nn as nn
    from torchvision.models import ResNet18_Weights, resnet18

    backbone = resnet18(weights=ResNet18_Weights.DEFAULT)
    backbone.fc = nn.Identity()
    backbone.eval()
    backbone.to(device)
    return backbone


def get_backbone(device: str = "cpu"):
    global _backbone, _backbone_device
    if _backbone is None or _backbone_device != device:
        _backbone = _build_backbone(device)
        _backbone_device = device
    return _backbone


def embed_roi_pair(
    pre_image: Image.Image,
    post_image: Image.Image,
    polygon,
    *,
    device: str = "cpu",
) -> Optional[List[float]]:
    crops = roi_crops(pre_image, post_image, polygon)
    if crops is None:
        return None
    import torch

    pre_crop, post_crop = crops
    transform = image_transform()
    pre_tensor = transform(pre_crop).unsqueeze(0).to(device)
    post_tensor = transform(post_crop).unsqueeze(0).to(device)
    diff_tensor = ((post_tensor - pre_tensor).clamp(-1.0, 1.0) + 1.0) / 2.0
    backbone = get_backbone(device)
    with torch.no_grad():
        pre_emb = backbone(pre_tensor).detach().cpu().numpy().astype("float32")[0]
        post_emb = backbone(post_tensor).detach().cpu().numpy().astype("float32")[0]
        diff_emb = backbone(diff_tensor).detach().cpu().numpy().astype("float32")[0]
    merged = list(pre_emb) + list(post_emb) + list(diff_emb)
    return [float(value) for value in merged]

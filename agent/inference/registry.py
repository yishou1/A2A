"""推理模型单例缓存与权重加载。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import torch

_CACHE: dict[str, Any] = {}
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT_DIR = PROJECT_ROOT / "models" / "checkpoints"


def _resolve_weights_path(config: dict[str, Any], key: str, default: str) -> str:
    weights = str(config.get(key, default))
    p = Path(weights)
    if p.is_file():
        return str(p.resolve())
    for candidate in (PROJECT_ROOT / weights, CHECKPOINT_DIR / Path(weights).name):
        if candidate.is_file():
            return str(candidate.resolve())
    return weights


def _load_state(module: torch.nn.Module, name: str) -> torch.nn.Module:
    path = CHECKPOINT_DIR / f"{name}.pt"
    if path.is_file():
        try:
            state = torch.load(path, map_location="cpu", weights_only=True)
        except TypeError:
            state = torch.load(path, map_location="cpu")
        module.load_state_dict(state, strict=False)
    return module


def get_device(config: dict[str, Any]) -> str:
    from agent.inference.utils import resolve_device

    return resolve_device(config)


def get_detector(config: dict[str, Any]):
    key = "detector"
    if key in _CACHE:
        return _CACHE[key]

    from ultralytics import RTDETR

    weights = _resolve_weights_path(config, "detection_model", "rtdetr-l.pt")
    model = RTDETR(weights)
    _CACHE[key] = model
    return model


def get_odconv_refiner(config: dict[str, Any]):
    key = "odconv"
    if key in _CACHE:
        return _CACHE[key]

    from agent.inference.models.odconv import ODConvRefiner

    device = get_device(config)
    model = _load_state(ODConvRefiner(), "odconv_refiner")
    model.eval().to(device)
    _CACHE[key] = model
    return model


def get_edl_head(config: dict[str, Any]):
    key = "edl"
    if key in _CACHE:
        return _CACHE[key]

    from agent.inference.models.edl_head import EvidentialHead

    device = get_device(config)
    model = EvidentialHead()
    ckpt = CHECKPOINT_DIR / "edl_head.pt"
    if ckpt.is_file():
        _load_state(model, "edl_head")
    model.eval().to(device)
    _CACHE[key] = model
    return model


def get_siamese_mask2former(config: dict[str, Any]):
    key = "mask2former"
    if key in _CACHE:
        return _CACHE[key]

    from agent.inference.models.siamese_mask2former import SiameseMask2Former

    model_id = config.get("mask2former_model", "facebook/mask2former-swin-tiny-ade-semantic")
    device = get_device(config)
    model = SiameseMask2Former(model_id).to_device(device)
    _CACHE[key] = model
    return model


def get_motr_tracker(config: dict[str, Any]):
    key = "motr"
    if key in _CACHE:
        return _CACHE[key]

    from agent.inference.models.motr_kalman import MOTRTracker

    device = get_device(config)
    model = _load_state(MOTRTracker(), "motr_tracker")
    model.eval().to(device)
    _CACHE[key] = model
    return model


def get_imagebind(config: dict[str, Any]):
    key = "imagebind"
    if key in _CACHE:
        return _CACHE[key]

    from agent.inference.models.imagebind_model import ImageBindEmbedder

    device = get_device(config)
    embedder = ImageBindEmbedder(device)
    _CACHE[key] = embedder
    return embedder


def get_mamba_fusion(config: dict[str, Any]):
    key = "mamba"
    if key in _CACHE:
        return _CACHE[key]

    from agent.inference.models.mamba_fusion import MultimodalMambaBlock

    dim = int(config.get("embed_dim", 1024))
    device = get_device(config)
    model = MultimodalMambaBlock(max(dim, 8))
    ckpt = CHECKPOINT_DIR / "mamba_fusion.pt"
    if ckpt.is_file():
        try:
            _load_state(model, "mamba_fusion")
        except RuntimeError:
            pass
    model.eval().to(device)
    _CACHE[key] = model
    return model


def get_supcon_meta(config: dict[str, Any]):
    key = "supcon"
    if key in _CACHE:
        return _CACHE[key]

    from agent.inference.models.supcon_meta import SupConMetaNet

    in_dim = int(config.get("embed_dim", 1024))
    device = get_device(config)
    model = SupConMetaNet(in_dim=in_dim)
    ckpt = CHECKPOINT_DIR / "supcon_meta.pt"
    if ckpt.is_file():
        try:
            _load_state(model, "supcon_meta")
        except RuntimeError:
            pass
    model.eval().to(device)
    _CACHE[key] = model
    return model


def get_semantic_comm(config: dict[str, Any]):
    key = "semantic_comm"
    if key in _CACHE:
        return _CACHE[key]

    from agent.inference.models.semantic_comm_net import KnowledgeSemanticCommNet

    model_id = config.get("semantic_comm_model", "google/flan-t5-small")
    device = get_device(config)
    model = KnowledgeSemanticCommNet(model_id).to_device(device)
    _CACHE[key] = model
    return model


def get_marl_policy(config: dict[str, Any]):
    key = "marl"
    if key in _CACHE:
        return _CACHE[key]

    from agent.inference.models.marl_policy import MARLPolicyNetwork

    device = get_device(config)
    model = _load_state(MARLPolicyNetwork(), "marl_policy")
    model.eval().to(device)
    _CACHE[key] = model
    return model


def get_page_encoder(config: dict[str, Any]):
    key = "page_encoder"
    if key in _CACHE:
        return _CACHE[key]

    from sentence_transformers import SentenceTransformer

    model_id = config.get("page_index_model", "paraphrase-MiniLM-L6-v2")
    model = SentenceTransformer(model_id)
    _CACHE[key] = model
    return model

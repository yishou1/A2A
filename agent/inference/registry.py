"""推理模型单例缓存与权重加载。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import torch

_CACHE: dict[str, Any] = {}
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT_DIR = PROJECT_ROOT / "models" / "checkpoints"


def clear_model_cache() -> None:
    """切换算力档位或权重路径后清空缓存（测试/热切换用）。"""
    _CACHE.clear()


def _resolve_weights_path(config: dict[str, Any], key: str, default: str) -> str:
    weights = str(config.get(key, default))
    p = Path(weights)
    if p.is_file():
        return str(p.resolve())
    for candidate in (PROJECT_ROOT / weights, CHECKPOINT_DIR / Path(weights).name):
        if candidate.is_file():
            return str(candidate.resolve())
    return weights


def _checkpoint_path(config: dict[str, Any], config_key: str, default_basename: str) -> Path:
    """从 inference.{config_key} 或 models/checkpoints/ 解析 .pt 路径。"""
    if config.get(config_key):
        return Path(_resolve_weights_path(config, config_key, default_basename))
    name = default_basename if default_basename.endswith(".pt") else f"{default_basename}.pt"
    return CHECKPOINT_DIR / name


def _load_state_dict(module: torch.nn.Module, path: Path) -> torch.nn.Module:
    if path.is_file():
        try:
            state = torch.load(path, map_location="cpu", weights_only=True)
        except TypeError:
            state = torch.load(path, map_location="cpu")
        module.load_state_dict(state, strict=False)
    return module


def _load_state(
    module: torch.nn.Module,
    default_basename: str,
    *,
    config: dict[str, Any] | None = None,
    config_key: str | None = None,
) -> torch.nn.Module:
    path = (
        _checkpoint_path(config or {}, config_key, default_basename)
        if config_key and config
        else CHECKPOINT_DIR / (
            default_basename if default_basename.endswith(".pt") else f"{default_basename}.pt"
        )
    )
    return _load_state_dict(module, path)


def _profile_tag(config: dict[str, Any]) -> str:
    return str(config.get("compute_profile", "medium"))


def get_device(config: dict[str, Any]) -> str:
    from agent.inference.utils import resolve_device

    return resolve_device(config)


def get_detector(config: dict[str, Any]):
    weights = _resolve_weights_path(config, "detection_model", "rtdetr-l.pt")
    key = f"detector:{weights}:{_profile_tag(config)}"
    if key in _CACHE:
        return _CACHE[key]

    from ultralytics import RTDETR

    model = RTDETR(weights)
    expected = config.get("detection_classes")
    if expected:
        names = list((model.names or {}).values())
        if names != list(expected):
            import warnings

            warnings.warn(
                f"detector class names {names} != config detection_classes {list(expected)} "
                f"(weights={weights})",
                stacklevel=2,
            )
    _CACHE[key] = model
    return model


def get_odconv_refiner(config: dict[str, Any]):
    ckpt = str(config.get("odconv_checkpoint", "odconv_refiner.pt"))
    key = f"odconv:{ckpt}:{_profile_tag(config)}"
    if key in _CACHE:
        return _CACHE[key]

    from agent.inference.models.odconv import ODConvRefiner

    device = get_device(config)
    model = _load_state(ODConvRefiner(), "odconv_refiner.pt", config=config, config_key="odconv_checkpoint")
    model.eval().to(device)
    _CACHE[key] = model
    return model


def get_edl_head(config: dict[str, Any]):
    ckpt = str(config.get("edl_checkpoint", "edl_head.pt"))
    key = f"edl:{ckpt}:{_profile_tag(config)}"
    if key in _CACHE:
        return _CACHE[key]

    from agent.inference.models.edl_head import EvidentialHead

    device = get_device(config)
    model = _load_state(EvidentialHead(), "edl_head.pt", config=config, config_key="edl_checkpoint")
    model.eval().to(device)
    _CACHE[key] = model
    return model


def get_siamese_mask2former(config: dict[str, Any]):
    model_id = config.get("mask2former_model", "facebook/mask2former-swin-tiny-ade-semantic")
    key = f"mask2former:{model_id}:{_profile_tag(config)}"
    if key in _CACHE:
        return _CACHE[key]

    from agent.inference.models.siamese_mask2former import SiameseMask2Former

    device = get_device(config)
    model = SiameseMask2Former(model_id).to_device(device)
    _CACHE[key] = model
    return model


def get_motr_tracker(config: dict[str, Any]):
    ckpt = str(config.get("motr_checkpoint", "motr_tracker.pt"))
    key = f"motr:{ckpt}:{_profile_tag(config)}"
    if key in _CACHE:
        return _CACHE[key]

    from agent.inference.models.motr_kalman import MOTRTracker

    device = get_device(config)
    model = _load_state(MOTRTracker(), "motr_tracker.pt", config=config, config_key="motr_checkpoint")
    model.eval().to(device)
    _CACHE[key] = model
    return model


def get_imagebind(config: dict[str, Any]):
    key = f"imagebind:{_profile_tag(config)}:{config.get('clip_fallback_model', 'clip')}"
    if key in _CACHE:
        return _CACHE[key]

    from agent.inference.models.imagebind_model import ImageBindEmbedder

    device = get_device(config)
    clip_id = str(config.get("clip_fallback_model", "openai/clip-vit-base-patch32"))
    embedder = ImageBindEmbedder(device, clip_model_id=clip_id)
    _CACHE[key] = embedder
    return embedder


def get_mamba_fusion(config: dict[str, Any]):
    dim = int(config.get("embed_dim", 1024))
    ckpt = str(config.get("mamba_checkpoint", "mamba_fusion.pt"))
    key = f"mamba:{dim}:{ckpt}:{_profile_tag(config)}"
    if key in _CACHE:
        return _CACHE[key]

    from agent.inference.models.mamba_fusion import MultimodalMambaBlock

    device = get_device(config)
    model = MultimodalMambaBlock(max(dim, 8))
    path = _checkpoint_path(config, "mamba_checkpoint", "mamba_fusion.pt")
    if path.is_file():
        try:
            _load_state_dict(model, path)
        except RuntimeError:
            pass
    model.eval().to(device)
    _CACHE[key] = model
    return model


def get_supcon_meta(config: dict[str, Any]):
    in_dim = int(config.get("embed_dim", 1024))
    ckpt = str(config.get("supcon_checkpoint", "supcon_meta.pt"))
    key = f"supcon:{in_dim}:{ckpt}:{_profile_tag(config)}"
    if key in _CACHE:
        return _CACHE[key]

    from agent.inference.models.supcon_meta import SupConMetaNet

    device = get_device(config)
    model = SupConMetaNet(in_dim=in_dim)
    path = _checkpoint_path(config, "supcon_checkpoint", "supcon_meta.pt")
    if path.is_file():
        try:
            _load_state_dict(model, path)
        except RuntimeError:
            pass
    model.eval().to(device)
    _CACHE[key] = model
    return model


def get_semantic_comm(config: dict[str, Any]):
    model_id = config.get("semantic_comm_model", "google/flan-t5-small")
    key = f"semantic_comm:{model_id}:{_profile_tag(config)}"
    if key in _CACHE:
        return _CACHE[key]

    from agent.inference.models.semantic_comm_net import KnowledgeSemanticCommNet

    device = get_device(config)
    model = KnowledgeSemanticCommNet(model_id).to_device(device)
    _CACHE[key] = model
    return model


def get_marl_policy(config: dict[str, Any]):
    ckpt = str(config.get("marl_checkpoint", "marl_policy.pt"))
    key = f"marl:{ckpt}:{_profile_tag(config)}"
    if key in _CACHE:
        return _CACHE[key]

    from agent.inference.models.marl_policy import MARLPolicyNetwork

    device = get_device(config)
    model = _load_state(MARLPolicyNetwork(), "marl_policy.pt", config=config, config_key="marl_checkpoint")
    model.eval().to(device)
    _CACHE[key] = model
    return model


def get_marl_ppo_scheduler(config: dict[str, Any]):
    ckpt = str(config.get("marl_ppo_checkpoint", "marl_ppo_scheduler.pt"))
    key = f"marl_ppo:{ckpt}:{_profile_tag(config)}"
    if key in _CACHE:
        return _CACHE[key]

    from agent.inference.models.marl_ppo_scheduler import MARLPPOSchedulerNet
    from agent.training.battlefield_scheduling_env import BattlefieldSchedulingEnv

    env = BattlefieldSchedulingEnv()
    device = get_device(config)
    model = MARLPPOSchedulerNet(
        obs_dim=env.obs_dim,
        n_actions=env.n_actions,
        n_agents=env.n_agents,
    )
    path = _checkpoint_path(config, "marl_ppo_checkpoint", "marl_ppo_scheduler.pt")
    if path.is_file():
        _load_state_dict(model, path)
    model.eval().to(device)
    _CACHE[key] = model
    return model


def get_page_encoder(config: dict[str, Any]):
    model_id = config.get("page_index_model", "paraphrase-MiniLM-L6-v2")
    key = f"page_encoder:{model_id}:{_profile_tag(config)}"
    if key in _CACHE:
        return _CACHE[key]

    from agent.inference.offline import is_offline_mode, resolve_model_ref
    from sentence_transformers import SentenceTransformer

    path = resolve_model_ref(model_id)
    local_only = is_offline_mode() or Path(path).is_dir()
    try:
        model = SentenceTransformer(path, local_files_only=local_only)
    except TypeError:
        model = SentenceTransformer(path)
    _CACHE[key] = model
    return model

"""线程安全的推理模型单例缓存与权重加载。"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

import torch

T = TypeVar("T")

_CACHE: dict[str, Any] = {}
_CACHE_LOCK = threading.RLock()
_CACHE_GENERATION = 0
_KEY_LOCKS: dict[str, threading.Lock] = {}
_KEY_LOCKS_LOCK = threading.Lock()
_EXECUTION_LOCKS: dict[str, threading.RLock] = {}
_EXECUTION_LOCKS_LOCK = threading.Lock()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT_DIR = PROJECT_ROOT / "models" / "checkpoints"


def clear_model_cache() -> None:
    """切换算力档位或权重路径后清空缓存（测试/热切换用）。"""
    global _CACHE_GENERATION
    with _CACHE_LOCK:
        _CACHE_GENERATION += 1
        _CACHE.clear()


def _key_lock(key: str) -> threading.Lock:
    with _KEY_LOCKS_LOCK:
        lock = _KEY_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _KEY_LOCKS[key] = lock
        return lock



def _get_or_create(key: str, factory: Callable[[], T]) -> T:
    """同一模型 key 的首次加载 single-flight，不同模型 key 可并行加载。"""
    with _CACHE_LOCK:
        if key in _CACHE:
            return _CACHE[key]

    lock = _key_lock(key)
    with lock:
        with _CACHE_LOCK:
            if key in _CACHE:
                return _CACHE[key]
            generation = _CACHE_GENERATION
        model = factory()
        with _CACHE_LOCK:
            # clear_model_cache 在加载期间发生时，不允许旧实例回填到新一代缓存。
            if _CACHE_GENERATION != generation:
                return model
            if key in _CACHE:
                return _CACHE[key]
            _CACHE[key] = model
        return model


def _execution_lock(resource: str, config: dict[str, Any]) -> threading.RLock:
    # 同一类共享模型串行执行，避免第三方模型内部可变 predictor/generation 状态发生竞争；
    # 不同资源（检测、跟踪、RAG 等）仍可在不同 mission 中并行。
    key = f"{resource}:{_profile_tag(config)}:{get_device(config)}"
    with _EXECUTION_LOCKS_LOCK:
        lock = _EXECUTION_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _EXECUTION_LOCKS[key] = lock
        return lock


@contextmanager
def inference_guard(resource: str, config: dict[str, Any]) -> Iterator[None]:
    lock = _execution_lock(resource, config)
    with lock:
        yield


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
    """从 inference.{config_key} 或 models/checkpoints/ 解析权重路径。"""
    if config.get(config_key):
        return Path(_resolve_weights_path(config, config_key, default_basename))
    name = default_basename if Path(default_basename).suffix else f"{default_basename}.pt"
    return CHECKPOINT_DIR / name


def _load_state_dict(
    module: torch.nn.Module,
    path: Path,
    *,
    strict: bool = False,
) -> torch.nn.Module:
    if path.is_file():
        if path.suffix == ".safetensors":
            from safetensors.torch import load_file

            state = load_file(str(path), device="cpu")
        else:
            try:
                state = torch.load(path, map_location="cpu", weights_only=True)
            except TypeError:
                state = torch.load(path, map_location="cpu")
        module.load_state_dict(state, strict=strict)
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
        else CHECKPOINT_DIR
        / (default_basename if default_basename.endswith(".pt") else f"{default_basename}.pt")
    )
    return _load_state_dict(module, path)


def _profile_tag(config: dict[str, Any]) -> str:
    return str(config.get("compute_profile", "medium"))


def get_device(config: dict[str, Any]) -> str:
    from agent.inference.utils import resolve_device

    return resolve_device(config)


def _model_key(prefix: str, config: dict[str, Any], identity: str) -> str:
    return f"{prefix}:{identity}:{_profile_tag(config)}:{get_device(config)}"


def get_detector(config: dict[str, Any]):
    weights = _resolve_weights_path(config, "detection_model", "rtdetr-l.pt")
    key = _model_key("detector", config, weights)

    def create():
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
        return model

    return _get_or_create(key, create)


def get_odconv_refiner(config: dict[str, Any]):
    ckpt = str(config.get("odconv_checkpoint", "odconv_refiner.pt"))
    key = _model_key("odconv", config, ckpt)

    def create():
        from agent.inference.models.odconv import ODConvRefiner

        device = get_device(config)
        model = _load_state(
            ODConvRefiner(), "odconv_refiner.pt", config=config, config_key="odconv_checkpoint"
        )
        return model.eval().to(device)

    return _get_or_create(key, create)


def get_edl_head(config: dict[str, Any]):
    ckpt = str(config.get("edl_checkpoint", "edl_head_s.safetensors"))
    key = _model_key("edl", config, ckpt)

    def create():
        from agent.inference.models.edl_head import EvidentialHead

        device = get_device(config)
        model = EvidentialHead()
        path = _checkpoint_path(config, "edl_checkpoint", "edl_head_s.safetensors")
        if not path.is_file():
            raise FileNotFoundError(f"trained EDL checkpoint not found: {path}")
        _load_state_dict(model, path, strict=True)
        return model.eval().to(device)

    return _get_or_create(key, create)


def get_siamese_mask2former(config: dict[str, Any]):
    model_id = str(config.get("mask2former_model", "facebook/mask2former-swin-tiny-ade-semantic"))
    key = _model_key("mask2former", config, model_id)

    def create():
        from agent.inference.models.siamese_mask2former import SiameseMask2Former

        return SiameseMask2Former(model_id).to_device(get_device(config))

    return _get_or_create(key, create)


def get_motr_tracker(config: dict[str, Any]):
    ckpt = str(config.get("motr_checkpoint", "motr_tracker.pt"))
    key = _model_key("motr", config, ckpt)

    def create():
        from agent.inference.models.motr_kalman import MOTRTracker

        device = get_device(config)
        model = _load_state(
            MOTRTracker(), "motr_tracker.pt", config=config, config_key="motr_checkpoint"
        )
        return model.eval().to(device)

    return _get_or_create(key, create)


def get_imagebind(config: dict[str, Any]):
    clip_id = str(config.get("clip_fallback_model", "openai/clip-vit-base-patch32"))
    key = _model_key("imagebind", config, clip_id)

    def create():
        from agent.inference.models.imagebind_model import ImageBindEmbedder

        return ImageBindEmbedder(get_device(config), clip_model_id=clip_id)

    return _get_or_create(key, create)


def get_mamba_fusion(config: dict[str, Any]):
    dim = int(config.get("embed_dim", 1024))
    ckpt = str(config.get("mamba_checkpoint", "mamba_fusion_s.safetensors"))
    key = _model_key("mamba", config, f"{dim}:{ckpt}")

    def create():
        from agent.inference.models.mamba_fusion import MultimodalMambaBlock

        device = get_device(config)
        model = MultimodalMambaBlock(max(dim, 8))
        path = _checkpoint_path(config, "mamba_checkpoint", "mamba_fusion_s.safetensors")
        if not path.is_file():
            raise FileNotFoundError(f"trained multimodal fusion checkpoint not found: {path}")
        _load_state_dict(model, path, strict=True)
        return model.eval().to(device)

    return _get_or_create(key, create)


def get_supcon_meta(config: dict[str, Any]):
    in_dim = int(config.get("embed_dim", 1024))
    ckpt = str(config.get("supcon_checkpoint", "supcon_meta_s.safetensors"))
    key = _model_key("supcon", config, f"{in_dim}:{ckpt}")

    def create():
        from agent.inference.models.supcon_meta import SupConMetaNet

        device = get_device(config)
        model = SupConMetaNet(in_dim=in_dim)
        path = _checkpoint_path(config, "supcon_checkpoint", "supcon_meta_s.safetensors")
        if not path.is_file():
            raise FileNotFoundError(f"trained SupCon checkpoint not found: {path}")
        _load_state_dict(model, path, strict=True)
        return model.eval().to(device)

    return _get_or_create(key, create)


def get_semantic_comm(config: dict[str, Any]):
    model_id = str(config.get("semantic_comm_model", "google/flan-t5-small"))
    key = _model_key("semantic_comm", config, model_id)

    def create():
        from agent.inference.models.semantic_comm_net import KnowledgeSemanticCommNet

        return KnowledgeSemanticCommNet(model_id).to_device(get_device(config))

    return _get_or_create(key, create)


def get_marl_policy(config: dict[str, Any]):
    ckpt = str(config.get("marl_checkpoint", "marl_policy.pt"))
    key = _model_key("marl", config, ckpt)

    def create():
        from agent.inference.models.marl_policy import MARLPolicyNetwork

        device = get_device(config)
        model = _load_state(
            MARLPolicyNetwork(), "marl_policy.pt", config=config, config_key="marl_checkpoint"
        )
        return model.eval().to(device)

    return _get_or_create(key, create)


def get_marl_ppo_scheduler(config: dict[str, Any]):
    ckpt = str(config.get("marl_ppo_checkpoint", "marl_ppo_scheduler_s.safetensors"))
    key = _model_key("marl_ppo", config, ckpt)

    def create():
        from agent.inference.models.marl_ppo_scheduler import MARLPPOSchedulerNet
        from agent.training.battlefield_scheduling_env import BattlefieldSchedulingEnv

        env = BattlefieldSchedulingEnv()
        device = get_device(config)
        model = MARLPPOSchedulerNet(
            obs_dim=env.obs_dim,
            n_actions=env.n_actions,
            n_agents=env.n_agents,
        )
        path = _checkpoint_path(config, "marl_ppo_checkpoint", "marl_ppo_scheduler_s.safetensors")
        if not path.is_file():
            raise FileNotFoundError(f"trained MARL-PPO checkpoint not found: {path}")
        _load_state_dict(model, path, strict=True)
        return model.eval().to(device)

    return _get_or_create(key, create)


def get_page_encoder(config: dict[str, Any]):
    model_id = str(config.get("page_index_model", "paraphrase-MiniLM-L6-v2"))
    key = _model_key("page_encoder", config, model_id)

    def create():
        from agent.inference.offline import is_offline_mode, resolve_model_ref
        from sentence_transformers import SentenceTransformer

        path = resolve_model_ref(model_id)
        local_only = is_offline_mode() or Path(path).is_dir()
        try:
            return SentenceTransformer(path, local_files_only=local_only)
        except TypeError:
            return SentenceTransformer(path)

    return _get_or_create(key, create)

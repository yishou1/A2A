"""离线部署：本地模型路径解析与 HuggingFace 加载辅助。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRETRAINED_ROOT = PROJECT_ROOT / "models" / "pretrained"
CHECKPOINT_ROOT = PROJECT_ROOT / "models" / "checkpoints"


def is_offline_mode() -> bool:
    """TIA_OFFLINE=1 或 HF_HUB_OFFLINE=1 时强制只读本地权重。"""
    return os.environ.get("TIA_OFFLINE", "0").lower() in ("1", "true", "yes") or os.environ.get(
        "HF_HUB_OFFLINE", "0"
    ).lower() in ("1", "true", "yes")


def _as_local_dir(model_ref: str) -> Path | None:
    ref = Path(model_ref)
    if ref.is_dir():
        return ref.resolve()
    for base in (PROJECT_ROOT, PRETRAINED_ROOT.parent, CHECKPOINT_ROOT.parent):
        candidate = (base / model_ref).resolve()
        if candidate.is_dir() and (candidate / "config.json").is_file():
            return candidate
    return None


def resolve_model_ref(model_ref: str, *, config: dict[str, Any] | None = None, config_key: str | None = None) -> str:
    """
    将 config 中的模型引用解析为可加载路径。
    优先：显式本地目录 > models/pretrained/<name> > 原 model_id（在线）
    """
    if config and config_key and config.get(config_key):
        model_ref = str(config[config_key])

    local = _as_local_dir(model_ref)
    if local is not None:
        return str(local)

    if is_offline_mode():
        slug = model_ref.replace("/", "--")
        for candidate in (
            PRETRAINED_ROOT / slug,
            PRETRAINED_ROOT / model_ref.split("/")[-1],
        ):
            if candidate.is_dir() and (candidate / "config.json").is_file():
                return str(candidate.resolve())

    return model_ref


def hf_from_pretrained(loader, model_ref: str, **kwargs):
    """transformers/sentence-transformers 统一加载：离线时 local_files_only=True。"""
    path = resolve_model_ref(model_ref, config=kwargs.pop("config", None), config_key=kwargs.pop("config_key", None))
    local = _as_local_dir(path) or (Path(path) if Path(path).is_dir() else None)
    if local is not None:
        return loader(str(local), local_files_only=True, **kwargs)
    if is_offline_mode():
        return loader(path, local_files_only=True, **kwargs)
    try:
        return loader(path, local_files_only=True, **kwargs)
    except OSError:
        return loader(path, **kwargs)

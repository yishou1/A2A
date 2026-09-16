"""算法库算力三档（与今早 param_tier / compute_profile 划分对齐）。

=======  ==========  ============================
profile  param_tier  含义
=======  ==========  ============================
small    low         同识别权重 + 低分辨率/FP16/关ODConv；认知侧轻量
medium   mid         同识别权重 + 中分辨率/FP16；认知侧中等
large    high        同识别权重 + 高分辨率/全精度；认知侧大模型
=======  ==========  ============================

切换优先级（与 Agent 一致）:
  1. 显式 ``param_tier`` / ``compute_profile`` / ``tier`` 参数
  2. 环境变量 ``TIA_COMPUTE_PROFILE``
  3. 默认 ``medium``（mid）

用法::

    from algorithms.compute_tiers import apply_tier, list_tiers, tier_summary

    cfg = apply_tier({}, tier="low")
    print(tier_summary(cfg))
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# 与 agent.inference.param_tiers 保持同名常量，避免算法库调用方必须先 import agent
PROFILE_ALIASES: dict[str, str] = {
    "low": "small",
    "s": "small",
    "mid": "medium",
    "m": "medium",
    "high": "large",
    "l": "large",
    "hi": "large",
}

PROFILE_TO_PARAM_TIER: dict[str, str] = {
    "small": "low",
    "medium": "mid",
    "large": "high",
    "offline": "mid",
}

VALID_PROFILES = frozenset({"small", "medium", "large", "offline"})

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = PROJECT_ROOT / "config" / "profiles"

# 算法库对外公布的三档卡片（今早划分）
COMPUTE_TIERS: dict[str, dict[str, Any]] = {
    "low": {
        "aliases": ["low", "small", "s"],
        "profile": "small",
        "param_tier": "low",
        "target_device": "8GB GPU / 边缘机",
        "description": "同识别权重轻推理：imgsz↓ / FP16 / 关 ODConv；毁伤与编码走轻量骨干",
        "recognition": {
            "detection_weight": "same_97class_best",
            "detection_imgsz": 416,
            "detection_half": True,
            "odconv_enable": False,
            "motr_checkpoint": "motr_tracker_battlefield.pt",
            "motr_variant": "high",
        },
        "cognition": {
            "embed_backend": "lightweight_low",
            "embed_dim": 256,
            "damage_backend": "lightweight",
            "mamba_checkpoint": "mamba_fusion_s.pt",
            "supcon_checkpoint": "supcon_meta_s.pt",
            "semantic_comm_model": "google/flan-t5-small",
        },
    },
    "mid": {
        "aliases": ["mid", "medium", "m"],
        "profile": "medium",
        "param_tier": "mid",
        "target_device": "12–16GB GPU",
        "description": "同识别权重中等推理：imgsz=640 / FP16 / 开 ODConv；编码 ResNet18",
        "recognition": {
            "detection_weight": "same_97class_best",
            "detection_imgsz": 640,
            "detection_half": True,
            "odconv_enable": True,
            "motr_checkpoint": "motr_tracker_battlefield.pt",
            "motr_variant": "high",
        },
        "cognition": {
            "embed_backend": "lightweight_mid",
            "embed_dim": 1024,
            "damage_backend": "mask2former",
            "mamba_checkpoint": "mamba_fusion.pt",
            "supcon_checkpoint": "supcon_meta.pt",
            "semantic_comm_model": "google/flan-t5-small",
        },
    },
    "high": {
        "aliases": ["high", "large", "l", "hi"],
        "profile": "large",
        "param_tier": "high",
        "target_device": "24GB+ GPU",
        "description": "同识别权重高精度：imgsz=1280 / FP32 / 开 ODConv；ImageBind + 大骨干",
        "recognition": {
            "detection_weight": "same_97class_best",
            "detection_imgsz": 1280,
            "detection_half": False,
            "odconv_enable": True,
            "motr_checkpoint": "motr_tracker_battlefield.pt",
            "motr_variant": "high",
        },
        "cognition": {
            "embed_backend": "imagebind",
            "embed_dim": 1024,
            "damage_backend": "mask2former",
            "mamba_checkpoint": "mamba_fusion_l.pt",
            "supcon_checkpoint": "supcon_meta_l.pt",
            "semantic_comm_model": "google/flan-t5-base",
        },
    },
}

# 哪些算法会受三档覆盖键影响（便于目录展示）
TIER_AFFECTED_ALGORITHMS: dict[str, tuple[str, ...]] = {
    "battlefield_rtdetr_detector": (
        "detection_model",
        "detection_imgsz",
        "detection_half",
        "odconv_enable",
        "odconv_crop_size",
        "confidence_threshold",
    ),
    "motr_neural_kalman_tracker": ("motr_checkpoint", "motr_variant"),
    "siamese_mask2former_damage": (
        "damage_backend",
        "damage_lightweight_checkpoint",
        "mask2former_model",
    ),
    "imagebind_multimodal_encoder": ("embed_backend", "embed_dim", "clip_fallback_model"),
    "multimodal_mamba_fusion": ("mamba_checkpoint",),
    "supcon_meta_classifier": ("supcon_checkpoint",),
    "synapse_rag_retriever": ("page_index_model",),
    "knowledge_semantic_comm": ("semantic_comm_model",),
    "edl_evidential_verifier": ("edl_checkpoint",),
    "anti_jam_mcdm_router": ("ppo_channel_checkpoint",),
    "marl_ppo_task_scheduler": ("marl_ppo_checkpoint",),
}


def normalize_profile_name(name: str) -> str:
    key = str(name or "").strip().lower()
    return PROFILE_ALIASES.get(key, key)


def normalize_param_tier(name: str) -> str:
    """任意别名 → low|mid|high。"""
    profile = normalize_profile_name(name)
    if profile in PROFILE_TO_PARAM_TIER:
        return PROFILE_TO_PARAM_TIER[profile]
    key = str(name or "").strip().lower()
    if key in COMPUTE_TIERS:
        return key
    return "mid"


def param_tier_for_profile(profile: str) -> str:
    return PROFILE_TO_PARAM_TIER.get(str(profile).lower(), "mid")


def profile_for_tier(tier: str) -> str:
    t = normalize_param_tier(tier)
    return str(COMPUTE_TIERS[t]["profile"])


def list_tiers() -> list[dict[str, Any]]:
    """算法库目录用：返回 low/mid/high 三档摘要。"""
    out: list[dict[str, Any]] = []
    for tier_id, card in COMPUTE_TIERS.items():
        out.append(
            {
                "tier": tier_id,
                "param_tier": card["param_tier"],
                "profile": card["profile"],
                "aliases": list(card["aliases"]),
                "target_device": card["target_device"],
                "description": card["description"],
                "recognition": dict(card["recognition"]),
                "cognition": dict(card["cognition"]),
                "profile_yaml": f"config/profiles/{card['profile']}.yaml",
            }
        )
    return out


def load_profile_overrides(profile: str) -> dict[str, Any]:
    """读取 config/profiles/{small|medium|large|offline}.yaml 的 inference 覆盖。"""
    name = normalize_profile_name(profile)
    if name not in VALID_PROFILES:
        name = profile_for_tier(profile)
    path = PROFILE_DIR / f"{name}.yaml"
    if not path.is_file():
        return {}
    try:
        import yaml
    except ImportError:
        return dict(COMPUTE_TIERS.get(normalize_param_tier(name), {}).get("recognition") or {})

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if "inference" in data:
        return dict(data.get("inference") or {})
    return {k: v for k, v in data.items() if k not in {"description", "target_device", "param_tier"}}


def resolve_tier_name(
    *,
    tier: str | None = None,
    config: dict[str, Any] | None = None,
    env: bool = True,
) -> str:
    """解析最终 param_tier（low|mid|high）。"""
    cfg = dict(config or {})
    explicit = tier or cfg.get("param_tier") or cfg.get("tier") or cfg.get("compute_profile")
    if explicit and str(explicit).strip().lower() not in {"auto", "detect", "automatic"}:
        return normalize_param_tier(str(explicit))
    if env:
        env_val = os.environ.get("TIA_COMPUTE_PROFILE", "").strip()
        if env_val and env_val.lower() not in {"auto", "detect", "automatic"}:
            return normalize_param_tier(env_val)
    return "mid"


def apply_tier(
    config: dict[str, Any] | None = None,
    *,
    tier: str | None = None,
    env: bool = True,
) -> dict[str, Any]:
    """把三档 profile 合并进扁平算法配置（供 algorithms.invoke / predict）。

    返回的 dict 同时含 ``param_tier`` 与 ``compute_profile``，可直接传给各算法 backend。
    """
    base = dict(config or {})
    # 若传入嵌套 inference，先摊平再合并
    if isinstance(base.get("inference"), dict):
        nested = dict(base.pop("inference"))
        base = {**nested, **base}

    param_tier = resolve_tier_name(tier=tier, config=base, env=env)
    profile = profile_for_tier(param_tier)
    overrides = load_profile_overrides(profile)
    merged = {
        **overrides,
        **base,  # 调用方显式键优先于档位默认
        "param_tier": param_tier,
        "compute_profile": profile,
        "compute_profile_requested": tier or base.get("compute_profile") or os.environ.get("TIA_COMPUTE_PROFILE") or profile,
    }
    return merged


def tier_summary(config: dict[str, Any] | None = None, *, tier: str | None = None) -> dict[str, Any]:
    """当前生效档位摘要（合并后）。"""
    cfg = apply_tier(config, tier=tier)
    tier_id = normalize_param_tier(str(cfg.get("param_tier", "mid")))
    card = COMPUTE_TIERS.get(tier_id, COMPUTE_TIERS["mid"])
    return {
        "param_tier": tier_id,
        "compute_profile": cfg.get("compute_profile"),
        "target_device": card["target_device"],
        "description": card["description"],
        "detection_model": cfg.get("detection_model"),
        "detection_imgsz": cfg.get("detection_imgsz"),
        "detection_half": cfg.get("detection_half"),
        "odconv_enable": cfg.get("odconv_enable"),
        "motr_variant": cfg.get("motr_variant"),
        "motr_checkpoint": cfg.get("motr_checkpoint"),
        "embed_backend": cfg.get("embed_backend"),
        "embed_dim": cfg.get("embed_dim"),
        "damage_backend": cfg.get("damage_backend"),
        "mask2former_model": cfg.get("mask2former_model"),
        "mamba_checkpoint": cfg.get("mamba_checkpoint"),
        "supcon_checkpoint": cfg.get("supcon_checkpoint"),
        "semantic_comm_model": cfg.get("semantic_comm_model"),
        "page_index_model": cfg.get("page_index_model"),
        "affected_algorithms": sorted(TIER_AFFECTED_ALGORITHMS.keys()),
    }


def algorithm_tier_keys(algorithm_id: str) -> tuple[str, ...]:
    return TIER_AFFECTED_ALGORITHMS.get(algorithm_id, ())


__all__ = [
    "COMPUTE_TIERS",
    "PROFILE_ALIASES",
    "PROFILE_TO_PARAM_TIER",
    "TIER_AFFECTED_ALGORITHMS",
    "VALID_PROFILES",
    "algorithm_tier_keys",
    "apply_tier",
    "list_tiers",
    "load_profile_overrides",
    "normalize_param_tier",
    "normalize_profile_name",
    "param_tier_for_profile",
    "profile_for_tier",
    "resolve_tier_name",
    "tier_summary",
]

"""算力档位（small / medium / large）配置合并。"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from agent.compute_detect import (
    AUTO_ALIASES,
    VALID_PROFILES,
    detect_compute_profile,
    probe_accelerator,
    resolve_compute_profile_detail,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = PROJECT_ROOT / "config" / "profiles"


def resolve_compute_profile(cfg: dict[str, Any]) -> str:
    """优先级: TIA_COMPUTE_PROFILE > inference.compute_profile > auto 探测。"""
    profile, _, _ = resolve_compute_profile_detail(cfg)
    return profile


def load_profile_overrides(profile: str) -> dict[str, Any]:
    path = PROFILE_DIR / f"{profile}.yaml"
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if "inference" in data:
        return dict(data.get("inference") or {})
    return {k: v for k, v in data.items() if k not in {"description", "target_device"}}


def apply_compute_profile(cfg: dict[str, Any]) -> dict[str, Any]:
    """将 profiles/{small|medium|large}.yaml 合并进 inference。"""
    out = dict(cfg)
    profile, requested, reason = resolve_compute_profile_detail(out)
    base_inf = dict(out.get("inference") or {})
    overrides = load_profile_overrides(profile)
    merged: dict[str, Any] = {
        **base_inf,
        **overrides,
        "compute_profile": profile,
        "compute_profile_requested": requested,
    }
    if requested in AUTO_ALIASES or reason != "manual":
        merged["compute_profile_auto_reason"] = reason
        logger.info("Compute profile auto-selected: %s (%s)", profile, reason)
    out["inference"] = merged
    return out


def profile_summary(cfg: dict[str, Any]) -> dict[str, Any]:
    inf = cfg.get("inference") or {}
    summary: dict[str, Any] = {
        "compute_profile": inf.get("compute_profile", "medium"),
        "compute_profile_requested": inf.get("compute_profile_requested"),
        "compute_profile_auto_reason": inf.get("compute_profile_auto_reason"),
        "detection_model": inf.get("detection_model"),
        "detection_imgsz": inf.get("detection_imgsz"),
        "embed_dim": inf.get("embed_dim"),
        "mask2former_model": inf.get("mask2former_model"),
        "semantic_comm_model": inf.get("semantic_comm_model"),
        "page_index_model": inf.get("page_index_model"),
    }
    gb, probe = probe_accelerator(cfg)
    if gb is not None:
        summary["accelerator_memory_gb"] = round(gb, 2)
    summary["accelerator_probe"] = probe
    return summary

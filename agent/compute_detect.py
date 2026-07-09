"""根据 GPU/CPU 算力自动选择 small / medium / large 档位。"""

from __future__ import annotations

from typing import Any

VALID_PROFILES = frozenset({"small", "medium", "large", "offline"})
AUTO_ALIASES = frozenset({"auto", "detect", "automatic"})

# 默认显存阈值（GB，按设备总显存）
DEFAULT_SMALL_MAX_GB = 10.0
DEFAULT_MEDIUM_MAX_GB = 22.0


def _inference_section(cfg: dict[str, Any]) -> dict[str, Any]:
    return dict(cfg.get("inference") or cfg)


def _cuda_device_index(inf: dict[str, Any]) -> int:
    dev = str(inf.get("device", "auto")).lower().strip()
    if dev == "cpu":
        return -1
    if dev.startswith("cuda:"):
        try:
            return int(dev.split(":", 1)[1])
        except ValueError:
            return 0
    return 0


def probe_accelerator(cfg: dict[str, Any]) -> tuple[float | None, str]:
    """
    探测加速器与可用显存（GB）。

    返回 (memory_gb, reason)；memory_gb 为 None 表示无 CUDA 显存可读（CPU/MPS/无 torch）。
    """
    inf = _inference_section(cfg)
    want = str(inf.get("device", "auto")).lower().strip()

    if want == "cpu":
        return None, "device=cpu"

    try:
        import torch
    except ImportError:
        return None, "torch_not_installed"

    if want.startswith("cuda") or (want == "auto" and torch.cuda.is_available()):
        idx = _cuda_device_index(inf)
        if idx < 0:
            return None, "device=cpu"
        if idx >= torch.cuda.device_count():
            return None, f"cuda:{idx}_unavailable"
        props = torch.cuda.get_device_properties(idx)
        gb = props.total_memory / (1024**3)
        return gb, f"cuda:{idx} {props.name} total={gb:.1f}GB"

    if want == "mps" or (want == "auto" and getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()):
        return None, "mps"

    return None, "no_accelerator"


def detect_compute_profile(cfg: dict[str, Any]) -> tuple[str, str]:
    """
    按算力推断档位。

    规则（可通过 inference.compute_profile_auto_thresholds 覆盖）:
      - CPU / 无 GPU → small
      - CUDA 总显存 ≤ small_max_gb → small
      - CUDA 总显存 ≤ medium_max_gb → medium
      - 更大 → large
      - MPS → mps_profile（默认 medium）
    """
    inf = _inference_section(cfg)
    thresholds = dict(inf.get("compute_profile_auto_thresholds") or {})
    small_max = float(thresholds.get("small_max_gb", DEFAULT_SMALL_MAX_GB))
    medium_max = float(thresholds.get("medium_max_gb", DEFAULT_MEDIUM_MAX_GB))
    if medium_max <= small_max:
        medium_max = small_max + 1.0

    gb, probe = probe_accelerator(cfg)

    if gb is not None:
        if gb <= small_max:
            return "small", f"{probe} -> small (<={small_max:.0f}GB)"
        if gb <= medium_max:
            return "medium", f"{probe} -> medium (<={medium_max:.0f}GB)"
        return "large", f"{probe} -> large (>{medium_max:.0f}GB)"

    if probe == "mps":
        mps_profile = str(thresholds.get("mps_profile", "medium")).lower()
        chosen = mps_profile if mps_profile in VALID_PROFILES else "medium"
        return chosen, f"{probe} -> {chosen} (mps_profile default)"

    return "small", f"{probe} -> small (fallback)"


def resolve_compute_profile_detail(cfg: dict[str, Any]) -> tuple[str, str, str]:
    """
    返回 (resolved_profile, requested, reason)。

    requested: 用户/环境变量原始值（如 auto、medium）
    reason: manual | 自动探测说明
    """
    import os

    inf = _inference_section(cfg)
    raw = os.environ.get("TIA_COMPUTE_PROFILE", inf.get("compute_profile", "auto"))
    requested = str(raw).strip().lower()

    if requested in VALID_PROFILES:
        return requested, requested, "manual"
    if requested in AUTO_ALIASES:
        profile, reason = detect_compute_profile(cfg)
        return profile, "auto", reason
    profile, reason = detect_compute_profile(cfg)
    return profile, requested, f"unknown profile '{requested}', auto: {reason}"

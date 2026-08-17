"""算力档位与自动探测测试。"""

from __future__ import annotations

import os
from pathlib import Path

from agent.compute_detect import detect_compute_profile, probe_accelerator
from agent.config_profiles import apply_compute_profile, resolve_compute_profile
from agent.pipeline import load_config

ROOT = Path(__file__).resolve().parents[1]


def test_profile_merge_small():
    cfg = {"inference": {"compute_profile": "small", "device": "cpu"}}
    merged = apply_compute_profile(cfg)
    inf = merged["inference"]
    assert inf["compute_profile"] == "small"
    assert inf["compute_profile_requested"] == "small"
    assert inf["embed_dim"] == 256
    assert inf["detection_imgsz"] == 416
    assert "mamba_fusion_s.pt" in inf["mamba_checkpoint"]


def test_auto_cpu_selects_small():
    cfg = {"inference": {"compute_profile": "auto", "device": "cpu"}}
    merged = apply_compute_profile(cfg)
    inf = merged["inference"]
    assert inf["compute_profile"] == "small"
    assert inf["compute_profile_requested"] == "auto"
    assert "compute_profile_auto_reason" in inf


def test_auto_thresholds():
    cfg = {
        "inference": {
            "compute_profile": "auto",
            "device": "cpu",
            "compute_profile_auto_thresholds": {"small_max_gb": 99},
        }
    }
    # CPU 仍走 small fallback；阈值只影响 CUDA 路径
    profile, reason = detect_compute_profile(cfg)
    assert profile == "small"
    assert "fallback" in reason or "cpu" in reason


def test_env_overrides_yaml():
    cfg = {"inference": {"compute_profile": "medium"}}
    os.environ["TIA_COMPUTE_PROFILE"] = "large"
    try:
        merged = apply_compute_profile(cfg)
        assert merged["inference"]["compute_profile"] == "large"
        assert merged["inference"]["embed_dim"] == 1536
    finally:
        os.environ.pop("TIA_COMPUTE_PROFILE", None)


def test_env_auto():
    cfg = {"inference": {"compute_profile": "medium", "device": "cpu"}}
    os.environ["TIA_COMPUTE_PROFILE"] = "auto"
    try:
        merged = apply_compute_profile(cfg)
        assert merged["inference"]["compute_profile"] == "small"
        assert merged["inference"]["compute_profile_requested"] == "auto"
    finally:
        os.environ.pop("TIA_COMPUTE_PROFILE", None)


def test_load_config_applies_profile():
    default = ROOT / "config" / "default.yaml"
    if not default.is_file():
        return
    os.environ["TIA_CONFIG"] = str(default)
    os.environ.pop("TIA_COMPUTE_PROFILE", None)
    try:
        cfg = load_config()
        assert resolve_compute_profile(cfg) in {"small", "medium", "large"}
        assert "embed_dim" in (cfg.get("inference") or {})
    finally:
        os.environ.pop("TIA_CONFIG", None)


def test_probe_accelerator_cpu():
    _, reason = probe_accelerator({"inference": {"device": "cpu"}})
    assert reason == "device=cpu"

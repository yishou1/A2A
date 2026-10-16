"""Compatibility checks for the CMS independent algorithm package."""

from __future__ import annotations

from algorithms import ALGORITHM_IDS, apply_tier, get_predict, invoke, list_tiers
from agent.inference.registry import detector_runtime_profile, resolve_detector_weights


def test_cms_catalog_exposes_independent_contracts() -> None:
    assert len(ALGORITHM_IDS) == 11
    assert "anti_jam_mcdm_router" in ALGORITHM_IDS
    assert all(callable(get_predict(algorithm_id)) for algorithm_id in ALGORITHM_IDS)
    assert [item["tier"] for item in list_tiers()] == ["low", "mid", "high"]


def test_three_tiers_keep_the_same_preferred_detector_weight() -> None:
    configs = [apply_tier({}, tier=tier) for tier in ("low", "mid", "high")]
    assert len({config["detection_model"] for config in configs}) == 1
    assert [config["detection_imgsz"] for config in configs] == [416, 640, 1280]
    assert [config["odconv_enable"] for config in configs] == [False, True, True]
    assert [config["param_tier"] for config in configs] == ["low", "mid", "high"]


def test_missing_finetune_weight_uses_bundled_detector_without_fake_odconv() -> None:
    config = apply_tier({}, tier="low")
    config["detection_model"] = "models/checkpoints/not-shipped-cms-weight.pt"
    assert resolve_detector_weights(config).name == "rtdetr-l.pt"
    profile = detector_runtime_profile(config)
    assert profile["detector_model"] == "rtdetr-l.pt"
    assert profile["odconv_enabled"] is False


def test_low_tier_mock_reports_disabled_odconv_and_router_is_callable() -> None:
    detected = invoke(
        "battlefield_rtdetr_detector",
        {"frames": [{"sensor_id": "EO-1", "modality": "eo_ir"}]},
        tier="low",
        use_mock=True,
    )
    assert detected["count"] == 1
    assert detected["detections"][0]["odconv_refined"] is False
    routed = invoke(
        "anti_jam_mcdm_router",
        {"packet": {"summary": "test"}},
        tier="mid",
        use_mock=True,
    )
    assert routed["route_mode"] == "mcdm_saw"
    assert routed["routes"]

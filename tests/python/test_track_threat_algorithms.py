from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "services"
sys.path.insert(0, str(SERVICES))

from track_threat_algorithms.app.main import app  # noqa: E402
from a2a_algorithms_common.service_predictors import (  # noqa: E402
    predict_graph_relation_reasoner,
    predict_multimodal_feature_fuser,
    predict_target_type_classifier,
    predict_track_state_updater,
    predict_trajectory_predictor,
)


TRACK_THREAT_CLASS_MAP = {
    "multimodal_feature_fuser": ("M03", "多模态融合"),
    "target_type_classifier": ("M04", "特征编码与分类"),
    "track_state_updater": ("M05", "多目标跟踪与定位"),
    "trajectory_predictor": ("M06", "时间序列预测"),
    "graph_relation_reasoner": ("M07", "图神经网络"),
}


def _sample_track() -> dict:
    return {
        "track_id": "trk-air-001",
        "object_type": "aircraft",
        "timestamp": 1718000050,
        "lat": 31.242,
        "lon": 121.512,
        "alt": 8800,
        "speed": 230,
        "heading": 86,
        "confidence": 0.92,
        "history_path": [
            {"timestamp": 1718000000, "lat": 31.230, "lon": 121.470, "alt": 8700, "speed": 225, "heading": 83, "confidence": 0.90},
            {"timestamp": 1718000010, "lat": 31.232, "lon": 121.478, "alt": 8720, "speed": 226, "heading": 84, "confidence": 0.91},
            {"timestamp": 1718000020, "lat": 31.235, "lon": 121.486, "alt": 8750, "speed": 228, "heading": 85, "confidence": 0.91},
            {"timestamp": 1718000030, "lat": 31.237, "lon": 121.495, "alt": 8780, "speed": 229, "heading": 86, "confidence": 0.92},
            {"timestamp": 1718000040, "lat": 31.240, "lon": 121.503, "alt": 8790, "speed": 230, "heading": 86, "confidence": 0.92},
            {"timestamp": 1718000050, "lat": 31.242, "lon": 121.512, "alt": 8800, "speed": 230, "heading": 86, "confidence": 0.92},
        ],
    }


def test_target_type_classifier_returns_type_scores() -> None:
    outputs = predict_target_type_classifier({"observations": [_sample_track()]}, {})

    item = outputs["classifications"][0]
    assert item["object_type"] == "aircraft"
    assert item["confidence"] > 0.8
    assert item["type_scores"]["aircraft"] >= item["type_scores"]["unknown"]


def test_trajectory_predictor_returns_predicted_path() -> None:
    outputs = predict_trajectory_predictor({"tracks": [_sample_track()]}, {"horizons_s": [10, 20, 30, 60]})

    prediction = outputs["predictions"][0]
    assert prediction["track_id"] == "trk-air-001"
    assert prediction["predicted_path"]
    assert {point["horizon_s"] for point in prediction["predicted_path"]} == {10, 20, 30, 60}
    assert prediction["model_family"] == "st_gnn"
    assert prediction["fallback_used"] is False
    assert prediction["model_runtime"]["backend"] == "torchscript"
    assert prediction["model_runtime"]["used"] is True


def test_algorithm_cards_use_track_threat_port_9022() -> None:
    for algorithm_id in TRACK_THREAT_CLASS_MAP:
        card_path = ROOT / "examples" / algorithm_id / "1.0.0" / "algorithm_card.yaml"
        card = card_path.read_text(encoding="utf-8")
        assert "127.0.0.1:9022" in card
        assert "127.0.0.1:9020" not in card
        assert f"algorithm_class: {TRACK_THREAT_CLASS_MAP[algorithm_id][0]}" in card
        assert "KG+Transformer" not in card
        assert "Enemy COA" not in card


def test_multimodal_feature_fuser_returns_feature_bundle() -> None:
    outputs = predict_multimodal_feature_fuser(
        {"detections": [_sample_track()], "tracks": [_sample_track()], "scene": {"protected_assets": [{"asset_id": "asset-001"}]}},
        {},
    )

    assert outputs["feature_version"] == "track_threat_features_v1"
    assert outputs["counts"]["detections"] == 1
    assert outputs["counts"]["tracks"] == 1
    assert outputs["counts"]["protected_assets"] == 1


def test_track_state_updater_creates_tracks() -> None:
    outputs = predict_track_state_updater({"detections": [_sample_track()], "existing_tracks": []}, {})

    assert outputs["tracks"]
    assert outputs["tracks"][0]["track_id"].startswith("trk-")
    assert outputs["summary"]["updated_count"] == 1


def test_graph_relation_reasoner_groups_close_tracks() -> None:
    track_a = _sample_track()
    track_b = dict(_sample_track())
    track_b["track_id"] = "trk-air-002"
    track_b["lat"] = track_a["lat"] + 0.01
    track_b["lon"] = track_a["lon"] + 0.01
    track_b["heading"] = track_a["heading"] + 2

    outputs = predict_graph_relation_reasoner({"tracks": [track_a, track_b]}, {})

    assert outputs["relations"]
    assert outputs["groups"]
    assert outputs["groups"][0]["member_track_ids"] == ["trk-air-001", "trk-air-002"]


def test_mounted_http_apps_match_algorithm_identity() -> None:
    client = TestClient(app)
    health_root = client.get("/health").json()
    assert health_root["mounted_algorithms"] == list(TRACK_THREAT_CLASS_MAP)
    for algorithm_id, (algorithm_class, algorithm_class_name) in TRACK_THREAT_CLASS_MAP.items():
        health = client.get(f"/{algorithm_id}/health").json()
        assert health["algorithm_id"] == algorithm_id
        assert health["status"] == "ready"
        metadata = client.get(f"/{algorithm_id}/metadata").json()
        assert metadata["algorithm_class"] == algorithm_class
        assert metadata["algorithm_class_name"] == algorithm_class_name
        assert metadata["owner_scope"] == "track_threat_agent"

        request_path = ROOT / "examples" / algorithm_id / "1.0.0" / "golden_cases" / "case_001_request.json"
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        result = client.post(f"/{algorithm_id}/predict", json=payload).json()
        assert result["ok"] is True
        assert result["algorithm_id"] == algorithm_id
        assert result["outputs"]

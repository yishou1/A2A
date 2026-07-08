from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "services"
sys.path.insert(0, str(SERVICES))

from a2a_algorithms_common.mission_feature_adapter import (  # noqa: E402
    build_features_from_agent_results,
    build_features_from_sc2le_proxy,
    verify_sc2le_proxy_no_result_leakage,
)
from a2a_algorithms_common.service_predictors import (  # noqa: E402
    mission_model_loaded,
    predict_closed_loop_decision_advisor,
    predict_execution_rule_matcher,
    predict_mission_completion_scorer,
    predict_mission_feature_adapter,
    predict_trajectory_linear_predictor,
    predict_xbd_damage_assessor,
    xbd_damage_model_loaded,
)

@pytest.fixture(scope="session")
def service_clients():
    from fastapi.testclient import TestClient

    from a2a_algorithms_common.http_service import create_algorithm_app
    from a2a_algorithms_common.service_predictors import (
        mission_model_loaded,
        predict_closed_loop_decision_advisor,
        predict_execution_control_planner,
        predict_execution_rule_matcher,
        predict_mission_completion_scorer,
        predict_mission_feature_adapter,
        predict_trajectory_linear_predictor,
        predict_xbd_damage_assessor,
        xbd_damage_model_loaded,
    )

    specs = [
        ("execution_rule_matcher", "decision", predict_execution_rule_matcher, lambda: True),
        ("trajectory_linear_predictor", "forecasting", predict_trajectory_linear_predictor, lambda: True),
        ("execution_control_planner", "planning", predict_execution_control_planner, lambda: True),
        ("mission_feature_adapter", "feature_engineering", predict_mission_feature_adapter, lambda: True),
        ("mission_completion_scorer", "scoring", predict_mission_completion_scorer, mission_model_loaded),
        ("closed_loop_decision_advisor", "decision", predict_closed_loop_decision_advisor, lambda: True),
        ("xbd_damage_assessor", "scoring", predict_xbd_damage_assessor, xbd_damage_model_loaded),
    ]
    clients = {}
    for algorithm_id, task_family, predict_fn, loaded_fn in specs:
        app = create_algorithm_app(algorithm_id, "1.0.0", task_family, predict_fn, model_loaded_callable=loaded_fn)
        clients[algorithm_id] = TestClient(app)
    return clients


def test_no_label_leakage_in_sc2le_proxy_features():
    leakage = verify_sc2le_proxy_no_result_leakage(
        mmr=3200.0,
        apm=150.0,
        duration_sec=900.0,
        opponent_mmr=3000.0,
    )
    assert leakage["passed"] is True


def test_strict_mode_insufficient_data():
    bundle = build_features_from_agent_results({}, mode="strict")
    assert bundle["assessment_status"] == "insufficient_data"
    assert bundle["missing_fields"]


def test_trajectory_linear_predictor_values():
    outputs = predict_trajectory_linear_predictor(
        {
            "track": {
                "track_id": "T-001",
                "history": [
                    {"t": 0.0, "x": 10.0, "y": 18.0},
                    {"t": 0.1, "x": 10.4, "y": 18.6},
                    {"t": 0.2, "x": 10.9, "y": 19.1},
                    {"t": 0.3, "x": 11.3, "y": 19.7},
                    {"t": 0.4, "x": 11.8, "y": 20.2},
                ],
                "weapon_prep_sec": 2.0,
                "flight_time_sec": 4.0,
            }
        },
        {},
    )
    assert outputs["velocity"]["vx"] == 4.5
    assert outputs["velocity"]["vy"] == 5.5
    assert outputs["aim_point"]["x"] == 38.78
    assert outputs["aim_point"]["y"] == 53.22
    assert outputs["execute_at"] == 2.4


def test_mission_model_loaded():
    assert mission_model_loaded() is True


def test_mission_completion_scorer_outputs():
    outputs = predict_mission_completion_scorer(
        {
            "features": {
                "damage_rate": 0.7,
                "asset_readiness": 0.8,
                "control_timeliness": 0.85,
                "intel_confidence": 0.9,
                "threat_pressure": 0.6,
                "ammo_pressure": 0.4,
                "comm_quality": 0.92,
            }
        },
        {},
    )
    assert outputs["model_source"] == "sc2le_proxy"
    assert outputs["feature_version"] == "mission_features_v2"
    assert outputs["mission_result"] in {"success", "failure"}
    assert outputs["assessment_status"] == "proxy_model_estimate"


def test_execution_rule_matcher_uses_fixture_rules_not_real_dataset_claim():
    outputs = predict_execution_rule_matcher(
        {
            "phase": "strike",
            "situation": {
                "threat_score": 0.75,
                "intel_confidence": 0.82,
                "resource_readiness": 0.81,
            },
        },
        {},
    )
    assert outputs["matched_rules"]
    assert outputs["primary_rule"]["consequent"]["executor_role"] == "artillery"


def test_http_health_metadata_predict(service_clients):
    for algorithm_id, client in service_clients.items():
        health = client.get("/health").json()
        assert health["algorithm_id"] == algorithm_id
        assert health["status"] == "ready"
        metadata = client.get("/metadata").json()
        assert metadata["backend_type"] == "python_http_service"
        request_path = ROOT / "examples" / algorithm_id / "1.0.0" / "golden_cases" / "case_001_request.json"
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        result = client.post("/predict", json=payload).json()
        assert result["ok"] is True
        assert result["outputs"]


def test_http_predict_missing_fields_returns_error(service_clients):
    payload = {
        "request_id": "req_bad",
        "trace_id": "trace_bad",
        "algorithm_id": "mission_completion_scorer",
        "version": "1.0.0",
        "inputs": {},
        "params": {},
    }
    result = service_clients["mission_completion_scorer"].post("/predict", json=payload).json()
    assert result["ok"] is False
    assert result["error"]


def test_closed_loop_advisor_action():
    outputs = predict_closed_loop_decision_advisor(
        {
            "target": {"target_id": "TGT-1", "threat_score": 0.8, "uncertainty": 0.1},
            "damage_probability": 0.9,
            "situation": "critical",
            "mission_completion": 0.95,
        },
        {},
    )
    assert outputs["action"] == "confirm_effect_and_shift"

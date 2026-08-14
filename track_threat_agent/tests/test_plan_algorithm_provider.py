import json
from pathlib import Path

import pytest

from app import main
from app.algorithm_library_runtime import AlgorithmLibraryError
from app.algorithm_provider import PlanAlgorithmProvider
from app.asset_impact_analyzer import AssetImpactAnalyzer
from app.group_detector import GroupDetector
from app.models import Detection
from app.threat_ranker import ThreatRanker
from app.tracker import MultiTargetTracker


DATA_DIR = Path(__file__).resolve().parents[1] / "sample_data"


def test_default_algorithm_provider_uses_project_plan_contract():
    provider = main.algorithm_provider

    assert isinstance(provider, PlanAlgorithmProvider)
    assert provider.mode == "llm_algolib_hybrid_runtime"

    contract = provider.algorithm_contract()
    assert contract["execution_strategy"] == "llm_planned_algorithm_library_with_local_fallback"
    assert contract["model_ownership"] == "algorithm_library_with_agent_local_fallback"
    assert contract["algorithm_library"]["base_url"] == "http://127.0.0.1:8088"
    assert contract["primary_algorithms"]["trajectory_prediction"] == "st_gnn_dynamic_entity_tracking"
    assert contract["primary_algorithms"]["threat_assessment"] == "dynamic_bayesian_network"
    assert "semantic_reasoning" not in contract["primary_algorithms"]
    assert contract["primary_algorithms"]["explainability"] == "xai_evidence_chain"
    assert contract["fallback_providers"]["trajectory_prediction"] == "adaptive_cv_ca_ct_physics"
    assert contract["training_status"]["dbn"]["parameter_version"] == "dbn-risk-attention-v1"
    assert "learned_trajectory_predictor" not in contract["training_status"]
    assert contract["algorithm_boundary"]["intent_inference"] == "downstream_agent"
    assert contract["network_algorithm_calls"] is False


@pytest.mark.anyio
async def test_artifact_exposes_plan_algorithm_trace_for_reporting():
    main.reset_runtime_state()
    payload = json.loads((DATA_DIR / "group_scene.json").read_text())
    task_payload = {
        "workflow_id": "wf-plan-algorithm",
        "work_item": "wi-plan-algorithm",
        "command": "analyze_perception_result",
        "role": "track_threat",
        "payload": payload,
    }

    body = await main.send_message(task_payload, token="unit-test")
    artifact = body["artifact"]
    summary = artifact["summary"]

    assert summary["algorithm_provider"]["mode"] == "llm_algolib_hybrid_runtime"
    assert summary["algorithm_provider"]["execution_strategy"] == "llm_planned_algorithm_library_with_local_fallback"
    assert summary["algorithm_provider"]["network_algorithm_calls"] is False
    assert summary["algorithm_provider"]["primary_algorithms"]["trajectory_prediction"] == "st_gnn_dynamic_entity_tracking"
    assert summary["algorithm_provider"]["fallback_providers"]["trajectory_prediction"] == "adaptive_cv_ca_ct_physics"

    first_track = artifact["tracks"][0]
    first_prediction = first_track["predicted_path"][0]
    first_threat = artifact["threats"][0]

    assert first_track["metadata"]["plan_algorithms"]["trajectory_prediction"]["algorithm"] == "ST-GNN"
    assert "st_gnn" not in first_prediction
    assert first_track["metadata"]["plan_algorithms"]["trajectory_prediction"]["applied"] is False
    assert first_track["metadata"]["plan_algorithms"]["trajectory_prediction"]["fallback_algorithm"] == "adaptive_multi_model_physics"
    assert first_threat["metadata"]["plan_algorithms"]["threat_assessment"]["algorithm"] == "DBN"
    assert first_threat["metadata"]["plan_algorithms"]["threat_assessment"]["runtime_provider"] == "dbn_risk_state_calibration_runtime"
    assert "semantic_reasoning" not in first_threat["metadata"]["plan_algorithms"]
    assert first_threat["metadata"]["xai"]["algorithm"] == "XAI"
    assert "semantic_sitrep" not in first_threat["metadata"]
    assert first_threat["metadata"]["dbn"]["risk_pattern_model"]["algorithm"] == "DBN observable-pattern calibration"
    assert first_threat["metadata"]["xai"]["factor_chain"]
    assert first_threat["metadata"]["xai"]["safety_chain"]
    assert artifact["decision_risk_assessments"]


class _FakeAlgorithmLibraryRuntime:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.fallbacks: list[tuple[str, str]] = []
        self.settings = type("Settings", (), {"enabled": True, "required": False})()

    def should_run(self, algorithm_id: str) -> bool:
        return algorithm_id == "trajectory_predictor"

    def run(self, algorithm_id: str, *, inputs: dict, params: dict) -> dict:
        assert algorithm_id == "trajectory_predictor"
        assert inputs["tracks"][0]["timestamp"] == 1000.0
        assert params["horizons_s"] == [10, 20, 30, 60, 600, 1200]
        if self.fail:
            raise AlgorithmLibraryError("remote unavailable")
        track_id = inputs["tracks"][0]["track_id"]
        return {
            "predictions": [
                {
                    "track_id": track_id,
                    "model_family": "st_gnn",
                    "model_version": "remote-test-v1",
                    "fallback_used": False,
                    "fallback_reason": "",
                    "predicted_path": [
                        {
                            "horizon_s": 10,
                            "lat": 31.5,
                            "lon": 121.6,
                            "alt": 7000.0,
                            "uncertainty_radius_m": 80.0,
                            "prediction_confidence": 0.91,
                        }
                    ],
                }
            ]
        }

    def record_local_fallback(self, algorithm_id: str, reason: str) -> None:
        self.fallbacks.append((algorithm_id, reason))

    def execution_trace(self) -> dict:
        return {"enabled": True, "executions": []}

    def status(self) -> dict:
        return {"enabled": True, "base_url": "http://127.0.0.1:8088"}


def _provider_with_runtime(runtime: _FakeAlgorithmLibraryRuntime) -> PlanAlgorithmProvider:
    return PlanAlgorithmProvider(
        MultiTargetTracker(),
        ThreatRanker(),
        AssetImpactAnalyzer(),
        GroupDetector(),
        algorithm_library_runtime=runtime,
    )


def _detection() -> Detection:
    return Detection(
        detection_id="det-remote-001",
        object_type="aircraft",
        timestamp=1000.0,
        lat=31.2,
        lon=121.4,
        alt=7000.0,
        speed=200.0,
        heading=90.0,
        confidence=0.9,
    )


def test_remote_trajectory_prediction_is_merged_into_track_state():
    provider = _provider_with_runtime(_FakeAlgorithmLibraryRuntime())

    track = provider.update_tracks([_detection()])[0]

    assert track.predicted_path[0]["lat"] == 31.5
    assert track.predicted_path[0]["lon"] == 121.6
    assert track.predicted_path[0]["model_used"] == "algorithm_library"
    assert track.predicted_path[0]["prediction_provenance"] == {
        "algorithm": "trajectory_predictor",
        "role": "primary",
        "is_trained_model": True,
        "execution_location": "zsl_algorithm_library",
    }
    assert track.metadata["algorithm_library"]["trajectory_predictor"]["model_version"] == "remote-test-v1"
    assert track.metadata["plan_algorithms"]["trajectory_prediction"]["runtime_provider"] == "zsl_algorithm_library"


def test_remote_trajectory_failure_keeps_local_prediction():
    runtime = _FakeAlgorithmLibraryRuntime(fail=True)
    provider = _provider_with_runtime(runtime)

    track = provider.update_tracks([_detection()])[0]

    assert track.predicted_path
    assert track.predicted_path[0]["lat"] != 31.5
    assert runtime.fallbacks == [("trajectory_predictor", "remote unavailable")]

import json
from pathlib import Path

import pytest
from protocol_contracts import validate_task_response

from app import main
from app.main import _requested_skills, send_message, verify_a2a_token, well_known_a2a_agent_card


DATA_DIR = Path(__file__).resolve().parents[1] / "sample_data"


def _commander_task(
    *,
    work_item: str,
    required_skill: str,
    output_hint: str,
    input_payload: dict,
) -> dict:
    return {
        "schema_version": "1.0",
        "workflow_id": "wf-commander-v1",
        "work_item": work_item,
        "command": required_skill,
        "required_skill": required_skill,
        "required_skills": [required_skill],
        "input": input_payload,
        "context": {"algorithm_level": "medium"},
        "attachments": [],
        "work_list": [],
        "output_hint": output_hint,
    }


def test_openapi_excludes_removed_frontend_demo_routes():
    paths = set(main.app.openapi()["paths"])

    assert {
        "/demo/frame",
        "/demo/state",
        "/demo/reset",
        "/demo/start",
        "/demo/stop",
        "/debug/reset",
    }.isdisjoint(paths)
    assert "/ws" not in {route.path for route in main.app.routes}
    assert "/sendMessage" in paths
    assert "/state/summary" in paths


def test_a2a_agent_card_compatibility_endpoint():
    card = well_known_a2a_agent_card()

    assert card["role"] == "track_threat"
    assert card["sendMessageEndpoint"] == "/sendMessage"
    assert card["sendMessageStreamEndpoint"] == "/sendMessageStream"
    skill_ids = {skill["id"] for skill in card["skills"]}
    assert {
        "track_threat_situation_analysis",
        "trajectory_tracking",
        "trajectory_prediction",
        "group_detection",
        "threat_ranking",
        "group_threat_ranking",
        "protected_asset_impact_analysis",
    } <= skill_ids
    assert card["execution"]["mode"] == "in_process_model_execution"
    assert card["execution"]["internal_workflow_engine"] is False
    assert card["modelsEndpoint"] == "/models"
    assert card["model_registry"]["count"] >= 7
    assert "track_state_kalman_cv" in {
        model["id"] for model in card["model_registry"]["models"]
    }


def test_models_endpoint_reports_agent_loaded_models():
    payload = main.models()

    assert payload["deployment_status"] in {"ready", "partial"}
    assert payload["count"] >= 7
    model_ids = {model["id"] for model in payload["models"]}
    assert "trajectory_adaptive_multi_model_physics" in model_ids
    assert "trajectory_imm" not in model_ids
    assert "local_graph_message_passing" not in model_ids
    assert "trajectory_numpy_sequence_predictor" not in model_ids
    assert all(model["status"] in {"ready", "unavailable"} for model in payload["models"])
    dbn = next(model for model in payload["models"] if model["id"] == "dbn_risk_state_calibration")
    assert dbn["version"] == "dbn-risk-attention-v1"


def test_output_schema_exposes_group_lifecycle_and_versioned_dbn_fields():
    schema = main.output_schema()

    assert "metadata.lifecycle_state" in schema["group_fields"]
    assert "parameter_model.sha256" in schema["dbn_fields"]


def test_commander_camel_case_required_skill_is_supported():
    assert _requested_skills({"requiredSkill": "trajectory_prediction"}) == [
        "trajectory_prediction"
    ]


@pytest.mark.anyio
async def test_send_message_accepts_a2a_task_payload():
    payload = json.loads((DATA_DIR / "group_scene.json").read_text())
    task_payload = {
        "workflow_id": "wf-test",
        "task_id": "wf-test:1:track_threat",
        "step_role": "track_threat",
        "command": "analyze_perception_result",
        "input": {
            "algorithm_level": "medium",
            "scene": payload["scene"],
            "detections": payload["detections"],
        },
    }

    body = await send_message(task_payload, token="unit-test")

    assert body["status"] == "completed"
    assert body["output"]["message_type"] == "track_threat_group_artifact"
    assert body["output"]["artifact"]["summary"]["track_count"] == len(payload["detections"])
    assert body["artifact"]["summary"]["track_count"] == len(payload["detections"])
    assert "prediction_eval" in body["artifact"]["summary"]
    assert body["artifact"]["decision_risk_assessments"]
    risk = body["artifact"]["decision_risk_assessments"][0]
    assert {
        "target_id",
        "priority",
        "risk",
        "threat_score",
        "probability",
        "rationale",
        "triggered_rules",
    } <= set(risk)
    assert 0.0 <= risk["probability"] <= 1.0
    assert 0.0 <= risk["threat_score"] <= 100.0
    assert body["artifact"]["artifact_schema_version"] == "track_threat_group_artifact/v1"
    assert body["artifact"]["trace"]["task_id"] == body["task_id"]
    assert body["artifact"]["summary"]["schema"]["artifact_schema_version"] == "track_threat_group_artifact/v1"
    prediction = body["artifact"]["tracks"][0]["predicted_path"][0]
    assert prediction["prediction_provenance"]["algorithm"] == "adaptive_multi_model_physics"
    assert "st_gnn_inspired" not in prediction
    assert body["artifact"]["summary"]["execution"]["mode"] == "in_process_model_execution"
    assert body["artifact"]["summary"]["execution"]["network_algorithm_calls"] is False


@pytest.mark.anyio
async def test_send_message_rejects_unsupported_required_skill():
    body = await send_message(
        {
            "workflow_id": "wf-unsupported",
            "task_id": "wf-unsupported:1",
            "work_item": "unsupported-1",
            "required_skill": "weapon_engagement",
            "input": {"detections": []},
        },
        token="unit-test",
    )

    assert body["status"] == "failed"
    assert body["error_code"] == "UNSUPPORTED_SKILL"


@pytest.mark.anyio
async def test_send_message_accepts_required_skills_context_and_output_hint():
    payload = json.loads((DATA_DIR / "frame_1.json").read_text())
    body = await send_message(
        {
            "workflow_id": "wf-skills",
            "task_id": "wf-skills:1",
            "work_item": "skills-1",
            "required_skills": ["trajectory_tracking", "trajectory_prediction"],
            "input": {"detections": payload["detections"]},
            "context": {"scene": payload["scene"], "algorithm_level": "medium"},
            "output_hint": {"include": ["tracks", "unified_threat_ranking"]},
        },
        token="unit-test",
    )

    assert body["status"] == "completed"
    assert body["executed_skills"] == ["trajectory_tracking", "trajectory_prediction"]
    assert body["output_hint_acknowledged"] == {"include": ["tracks", "unified_threat_ranking"]}


@pytest.mark.anyio
async def test_commander_trajectory_tracking_skill_returns_requested_output_key():
    main.reset_runtime_state()
    payload = json.loads((DATA_DIR / "frame_1.json").read_text())
    task = _commander_task(
        work_item="wi-commander-tracking",
        required_skill="trajectory_tracking",
        output_hint="tracking_result",
        input_payload={
            "cognition_result": [
                {
                    "activity_id": "cognition-1",
                    "value": {
                        "task_id": payload["task_id"],
                        "scene": payload["scene"],
                        "detections": payload["detections"],
                    },
                }
            ]
        },
    )

    body = await send_message(task, token="unit-test")

    validate_task_response(task, body)
    assert body["schema_version"] == "1.0"
    assert body["status"] == "completed"
    tracking_result = body["output"]["tracking_result"]
    assert tracking_result["schema_version"] == "tracking_result/v1"
    assert len(tracking_result["tracks"]) == len(payload["detections"])
    assert tracking_result["scene"] == payload["scene"]
    assert body["executed_skills"] == ["trajectory_tracking"]


@pytest.mark.anyio
async def test_commander_threat_ranking_consumes_tracking_context_without_retracking():
    main.reset_runtime_state()
    payload = json.loads((DATA_DIR / "group_scene.json").read_text())
    tracking_task = _commander_task(
        work_item="wi-commander-track-first",
        required_skill="trajectory_tracking",
        output_hint="tracking_result",
        input_payload={
            "scene": payload["scene"],
            "detections": payload["detections"],
        },
    )
    tracking_response = await send_message(tracking_task, token="unit-test")
    tracking_result = tracking_response["output"]["tracking_result"]
    history_lengths_before = {
        item["track_id"]: len(item["history_path"])
        for item in tracking_result["tracks"]
    }

    ranking_task = _commander_task(
        work_item="wi-commander-rank-second",
        required_skill="threat_ranking",
        output_hint="threat_assessment_result",
        input_payload={
            "tracking_result": [
                {
                    "activity_id": "tracking-1",
                    "work_item": "wi-commander-track-first",
                    "value": tracking_result,
                }
            ]
        },
    )
    ranking_response = await send_message(ranking_task, token="unit-test")

    validate_task_response(ranking_task, ranking_response)
    assessment = ranking_response["output"]["threat_assessment_result"]
    assert ranking_response["schema_version"] == "1.0"
    assert assessment["schema_version"] == "threat_assessment_result/v1"
    assert assessment["threats"]
    assert assessment["unified_threat_ranking"]
    assert assessment["decision_risk_assessments"]
    assert {
        track_id: len(main.tracker.tracks[track_id].history_path)
        for track_id in history_lengths_before
    } == history_lengths_before


@pytest.mark.anyio
async def test_full_pipeline_honors_string_output_hint():
    main.reset_runtime_state()
    payload = json.loads((DATA_DIR / "frame_1.json").read_text())
    task = _commander_task(
        work_item="wi-commander-full",
        required_skill="track_threat_situation_analysis",
        output_hint="track_threat_group_artifact",
        input_payload=payload,
    )

    body = await send_message(task, token="unit-test")

    validate_task_response(task, body)
    artifact = body["output"]["track_threat_group_artifact"]
    assert artifact["artifact_schema_version"] == "track_threat_group_artifact/v1"
    assert artifact["summary"]["track_count"] == len(payload["detections"])


@pytest.mark.anyio
async def test_state_summary_exposes_runtime_and_last_artifact():
    main.reset_runtime_state()
    payload = json.loads((DATA_DIR / "frame_1.json").read_text())
    result = main._process_payload(main.PerceptionResultRequest.model_validate(payload))

    summary = main.state_summary()

    assert summary["status"] == "ok"
    assert summary["schema"]["state_schema_version"] >= 1
    assert summary["last_artifact"]["task_id"] == result["task_id"]
    assert summary["last_artifact"]["track_count"] == result["artifact"]["summary"]["track_count"]
    assert "learned_trajectory_predictor" not in summary["model_status"]
    assert summary["model_status"]["physics_fallback"]["is_trained_model"] is False


def test_send_message_requires_bearer_token():
    with pytest.raises(Exception):
        verify_a2a_token(None)


@pytest.mark.parametrize(
    "filename",
    [
        "scene_01_normal_tracking.json",
        "scene_02_asset_approach.json",
        "scene_03_group_maneuver.json",
    ],
)
def test_standard_integration_scenes_are_processable(filename):
    main.algorithm_provider.reset()
    payload = json.loads((DATA_DIR / filename).read_text(encoding="utf-8"))

    result = main._process_payload(main.PerceptionResultRequest.model_validate(payload))

    assert result["status"] == "completed"
    assert result["artifact"]["summary"]["track_count"] == len(payload["detections"])
    assert result["artifact"]["artifact_schema_version"] == "track_threat_group_artifact/v1"
    assert result["artifact"]["protected_assets"]
    assert result["artifact"]["unified_threat_ranking"]
    top = result["artifact"]["unified_threat_ranking"][0]
    assert top["reason"]
    assert isinstance(top["evidence"], list)
    assert isinstance(top["factors"], dict)
    if top["item_type"] == "asset_impact":
        assert "eta_to_protected_radius_s" in top
        assert "will_enter_protection_radius" in top


def test_input_output_schemas_document_assets_and_ranking_fields():
    input_payload = main.input_schema()
    output_payload = main.output_schema()

    assert "protected_assets" in input_payload["scene_fields"]
    assert "asset_impacts" in output_payload["artifact_fields"]
    assert "reason" in output_payload["unified_threat_ranking_fields"]
    assert "eta_to_protected_radius_s" in output_payload["asset_impact_fields"]


@pytest.mark.anyio
async def test_send_message_does_not_apply_legacy_ridge_or_untrained_graph_predictors():
    main.reset_runtime_state()

    bodies = []
    for frame_index in range(4):
        timestamp = 1_782_400_000.0 + frame_index * 10.0
        payload = {
            "task_id": f"task-learned-online-{frame_index}",
            "message_type": "perception_result",
            "algorithm_level": "medium",
            "scene": {
                "protected_zone_lat": 31.2304,
                "protected_zone_lon": 121.4737,
                "protected_radius_m": 30_000,
                "protected_assets": [],
            },
            "detections": [
                {
                    "detection_id": f"learned-aircraft-{frame_index}",
                    "object_type": "aircraft",
                    "timestamp": timestamp,
                    "lat": 31.0 + frame_index * 0.011,
                    "lon": 121.0 + frame_index * 0.018,
                    "alt": 10_000.0,
                    "speed": 230.0,
                    "heading": 58.0,
                    "confidence": 0.96,
                    "source_agent": "opensky_replay",
                    "metadata": {"demo": "formal_prediction_chain"},
                }
            ],
        }
        task_payload = {
            "workflow_id": "wf-learned-online",
            "work_item": f"wi-learned-online-{frame_index}",
            "command": "analyze_perception_result",
            "role": "track_threat",
            "payload": payload,
        }
        bodies.append(await send_message(task_payload, token="unit-test"))

    artifact = bodies[-1]["artifact"]
    assert artifact["summary"]["track_count"] == 1
    assert "learned_trajectory_predictor" not in artifact["summary"]["algorithm_provider"]["training_status"]

    track = artifact["tracks"][0]
    assert all(point.get("model_used") != "learned_numpy_sequence_predictor" for point in track["predicted_path"])
    assert all(not str(point.get("model_used", "")).endswith("_graph_refined") for point in track["predicted_path"])
    assert "learned_predictor" not in track["metadata"]


def test_process_payload_applies_embedded_torchscript_st_gnn_when_history_is_available():
    pytest.importorskip("torch")
    model_status = main.trained_st_gnn_runtime.status()
    if not model_status["models"].get("aircraft", {}).get("loaded"):
        pytest.skip(f"embedded aircraft ST-GNN model is unavailable: {model_status}")

    main.algorithm_provider.reset()
    result = None
    for frame_index in range(6):
        timestamp = 1_782_500_000.0 + frame_index * 10.0
        payload = {
            "task_id": f"task-embedded-st-gnn-{frame_index}",
            "message_type": "perception_result",
            "algorithm_level": "medium",
            "scene": {
                "protected_zone_lat": 31.2304,
                "protected_zone_lon": 121.4737,
                "protected_radius_m": 30_000,
                "protected_assets": [],
            },
            "detections": [
                {
                    "detection_id": f"embedded-aircraft-{frame_index}",
                    "object_type": "aircraft",
                    "timestamp": timestamp,
                    "lat": 31.0,
                    "lon": 121.0 + frame_index * 0.015,
                    "alt": 9_000.0,
                    "speed": 150.0,
                    "heading": 90.0,
                    "confidence": 0.97,
                    "source_agent": "embedded_st_gnn_test",
                    "metadata": {},
                }
            ],
        }
        result = main._process_payload(main.PerceptionResultRequest.model_validate(payload))

    assert result is not None
    track = result["artifact"]["tracks"][0]
    by_horizon = {point["dt_s"]: point for point in track["predicted_path"]}
    assert by_horizon[10.0]["model_used"] == "st_gnn_torchscript"
    assert by_horizon[10.0]["model_version"] == "st_gnn_aircraft_kaggle_v1"
    assert by_horizon[10.0]["uncertainty_radius_m"] > 0.0
    assert by_horizon[10.0]["fallback_reason"] is None
    assert by_horizon[10.0]["st_gnn"]["runtime_provider"] == "torchscript_pytorch"
    assert by_horizon[10.0]["st_gnn"]["trained_model_loaded"] is True
    assert track["metadata"]["st_gnn_runtime"]["applied"] is True
    assert track["metadata"]["plan_algorithms"]["trajectory_prediction"]["trained_model_loaded"] is True

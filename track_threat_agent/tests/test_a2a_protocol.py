import json
from pathlib import Path

import pytest

from app import main
from app.main import _requested_skills, send_message, verify_a2a_token, well_known_a2a_agent_card


DATA_DIR = Path(__file__).resolve().parents[1] / "sample_data"


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
    assert body["artifact"]["tracks"][0]["predicted_path"][0]["st_gnn_inspired"] in {True, False}


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
async def test_state_summary_exposes_runtime_and_last_artifact():
    await main.demo_reset()
    payload = json.loads((DATA_DIR / "frame_1.json").read_text())
    result = main._process_payload(main.PerceptionResultRequest.model_validate(payload))

    summary = main.state_summary()

    assert summary["status"] == "ok"
    assert summary["schema"]["state_schema_version"] >= 1
    assert summary["last_artifact"]["task_id"] == result["task_id"]
    assert summary["last_artifact"]["track_count"] == result["artifact"]["summary"]["track_count"]
    assert summary["model_status"]["learned_trajectory_predictor"]["loaded"] in {True, False}


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
async def test_send_message_applies_learned_predictor_after_track_history_is_available():
    assert main.learned_predictor.loaded is True
    await main.demo_reset()

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
                    "metadata": {"demo": "learned_predictor_online"},
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
    assert artifact["summary"]["algorithm_provider"]["training_status"]["learned_trajectory_predictor"]["loaded"] is True

    track = artifact["tracks"][0]
    learned_points = [
        point
        for point in track["predicted_path"]
        if point.get("model_used") == "learned_numpy_sequence_predictor"
    ]
    assert learned_points
    assert {point["dt_s"] for point in learned_points} >= {10.0, 20.0, 30.0, 60.0}
    assert all(point["learned_model"]["loaded"] is True for point in learned_points)
    assert track["metadata"]["learned_predictor"]["applied"] is True
    assert track["metadata"]["plan_algorithms"]["trajectory_prediction"]["trained_model_loaded"] is True


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
    assert by_horizon[10.0]["model_version"] == "st_gnn_aircraft_kaggle_v1_candidate"
    assert by_horizon[10.0]["uncertainty_radius_m"] > 0.0
    assert by_horizon[10.0]["fallback_reason"] is None
    assert by_horizon[10.0]["st_gnn"]["runtime_provider"] == "torchscript_pytorch"
    assert by_horizon[10.0]["st_gnn"]["trained_model_loaded"] is True
    assert track["metadata"]["st_gnn_runtime"]["applied"] is True
    assert track["metadata"]["plan_algorithms"]["trajectory_prediction"]["trained_model_loaded"] is True

import json
from pathlib import Path

import pytest

from app.main import send_message, verify_a2a_token, well_known_a2a_agent_card


DATA_DIR = Path(__file__).resolve().parents[1] / "sample_data"


def test_a2a_agent_card_compatibility_endpoint():
    card = well_known_a2a_agent_card()

    assert card["role"] == "track_threat"
    assert card["sendMessageEndpoint"] == "/sendMessage"
    assert card["sendMessageStreamEndpoint"] == "/sendMessageStream"


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
    assert body["artifact"]["tracks"][0]["predicted_path"][0]["st_gnn_inspired"] in {True, False}


def test_send_message_requires_bearer_token():
    with pytest.raises(Exception):
        verify_a2a_token(None)

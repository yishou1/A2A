import json
from pathlib import Path

import pytest

from app import main
from app.algorithm_provider import LocalBuiltInAlgorithmProvider


DATA_DIR = Path(__file__).resolve().parents[1] / "sample_data"


def _task_payload(work_item: str = "wi-track-threat-001") -> dict:
    payload = json.loads((DATA_DIR / "group_scene.json").read_text())
    return {
        "workflow_id": "wf-near-real",
        "work_item": work_item,
        "command": "analyze_perception_result",
        "role": "track_threat",
        "work_list": [
            {"activity": "perception_fusion", "role": "recon"},
            {"activity": "track_threat_analysis", "role": "track_threat"},
            {"activity": "situation_display", "role": "commander"},
        ],
        "payload": {
            "task_id": "near-real-task-001",
            "message_type": "perception_result",
            "algorithm_level": "medium",
            "scene": payload["scene"],
            "detections": payload["detections"],
        },
    }


@pytest.mark.anyio
async def test_send_message_is_idempotent_by_work_item():
    await main.demo_reset()
    task_payload = _task_payload()

    first = await main.send_message(task_payload, token="unit-test")
    track_id = first["artifact"]["tracks"][0]["track_id"]
    first_history_len = len(first["artifact"]["tracks"][0]["history_path"])

    second = await main.send_message(task_payload, token="unit-test")
    current_track = main.tracker.tracks[track_id]

    assert second["cached"] is True
    assert second["work_item"] == task_payload["work_item"]
    assert len(current_track.history_path) == first_history_len
    assert main.runtime_status()["processed_task_count"] == 1


@pytest.mark.anyio
async def test_workflow_work_list_is_captured_and_queryable():
    await main.demo_reset()
    task_payload = _task_payload("wi-track-threat-002")

    await main.send_message(task_payload, token="unit-test")
    body = main.workflow_work_list("wf-near-real")

    assert body["workflow_id"] == "wf-near-real"
    assert body["role"] == "track_threat"
    assert body["work_list"][1]["activity"] == "track_threat_analysis"


def test_health_exposes_near_real_agent_runtime_fields():
    body = main.health()

    assert body["agent_status"] in {"idle", "busy", "error"}
    assert "processed_task_count" in body
    assert "cached_work_item_count" in body
    assert "algorithm_provider" in body


def test_local_builtin_algorithm_provider_has_stable_mode_name():
    provider = LocalBuiltInAlgorithmProvider(main.tracker, main.graph_predictor, main.ranker, main.impact_analyzer, main.group_detector)

    assert provider.mode == "local_builtin"

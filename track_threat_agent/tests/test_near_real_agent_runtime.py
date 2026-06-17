import json
from pathlib import Path

import pytest

from app import main
from app.algorithm_provider import LocalBuiltInAlgorithmProvider
from app.a2a_runtime import A2ARuntimeState
from app.state_store import FileStateStore


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


def test_ready_endpoint_reports_dispatch_readiness():
    body = main.ready()

    assert body["ready"] is True
    assert body["agent"] == "track-threat-group-agent"
    assert body["role"] == "track_threat"
    assert body["agent_status"] in {"idle", "busy", "error"}


def test_metrics_endpoint_exposes_runtime_counters():
    body = main.metrics()

    assert body["agent"] == "track-threat-group-agent"
    assert body["role"] == "track_threat"
    assert "tasks_completed" in body
    assert "cache_hits" in body
    assert "uptime_seconds" in body


@pytest.mark.anyio
async def test_send_message_returns_standard_a2a_response_envelope():
    await main.demo_reset()
    task_payload = _task_payload("wi-envelope-001")

    response = await main.send_message(task_payload, token="unit-test")

    assert response["workflow_id"] == task_payload["workflow_id"]
    assert response["work_item"] == task_payload["work_item"]
    assert response["agent"] == "track-threat-group-agent"
    assert response["role"] == "track_threat"
    assert response["command"] == "analyze_perception_result"
    assert response["status"] == "completed"
    assert response["error"] is None
    assert response["output"]["message_type"] == "track_threat_group_artifact"
    assert response["output"]["artifact"]["summary"]["track_count"] == 7
    assert response["metrics"]["track_count"] == 7
    assert response["metrics"]["duration_ms"] == response["metrics"]["latency_ms"]
    assert response["cached"] is False


@pytest.mark.anyio
async def test_send_message_ready_false_returns_standard_failure_envelope():
    await main.demo_reset()
    task_payload = _task_payload("wi-not-ready-001")

    main.set_ready({"ready": False})
    try:
        response = await main.send_message(task_payload, token="unit-test")
    finally:
        main.set_ready({"ready": True})

    assert response["workflow_id"] == task_payload["workflow_id"]
    assert response["work_item"] == task_payload["work_item"]
    assert response["status"] == "failed"
    assert response["output"] == {}
    assert response["error"] == "agent is not ready"
    assert response["error_code"] == "AGENT_NOT_READY"
    assert response["message"] == "agent is not ready"
    assert response["cached"] is False


def test_local_builtin_algorithm_provider_has_stable_mode_name():
    provider = LocalBuiltInAlgorithmProvider(main.tracker, main.graph_predictor, main.ranker, main.impact_analyzer, main.group_detector)

    assert provider.mode == "local_builtin"


def test_runtime_state_can_export_and_restore_persistent_state():
    runtime = A2ARuntimeState(agent_name="agent-a", role="track_threat")
    runtime.capture_work_list(
        {
            "workflow_id": "wf-persist",
            "work_list": [{"activity": "track_threat_analysis", "role": "track_threat"}],
        }
    )
    runtime.set_task_response(
        "work-001",
        {
            "status": "Completed",
            "work_item": "work-001",
            "artifact_summary": {"track_count": 7},
            "cached": False,
        },
    )
    runtime.set_stream_events("work-001", ["data: one\n\n"])

    restored = A2ARuntimeState(agent_name="agent-a", role="track_threat")
    restored.restore_persistent_state(runtime.export_persistent_state())

    cached = restored.get_task_response("work-001")
    assert cached["cached"] is True
    assert cached["artifact_summary"]["track_count"] == 7
    assert restored.get_stream_events("work-001") == ["data: one\n\n"]
    assert restored.get_work_list("wf-persist")[0]["activity"] == "track_threat_analysis"
    assert restored.snapshot()["processed_task_count"] == 1


@pytest.mark.anyio
async def test_send_message_saves_and_restores_agent_state_snapshot(tmp_path):
    old_store = main.state_store
    main.state_store = FileStateStore(tmp_path / "agent_state.json")
    try:
        await main.demo_reset()
        task_payload = _task_payload("wi-persist-001")
        first = await main.send_message(task_payload, token="unit-test")
        track_id = first["artifact"]["tracks"][0]["track_id"]

        assert main.state_store.path.exists()

        main.tracker.reset()
        main.group_detector.reset()
        main.runtime.reset_runtime()
        main.last_artifact = {"tracks": [], "summary": {"track_count": 0}}

        restored = main._restore_state_snapshot()

        assert restored is True
        assert track_id in main.tracker.tracks
        assert main.runtime.get_task_response("wi-persist-001")["cached"] is True
        assert main.last_artifact["summary"]["track_count"] == 7
    finally:
        main.state_store = old_store
        await main.demo_reset()

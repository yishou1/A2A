from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import main
from app.a2a_runtime import A2ARuntimeState
from app.nacos_register import NacosRegistrar, NacosSettings


DATA_DIR = Path(__file__).resolve().parents[1] / "sample_data"


def _task(work_item: str) -> dict:
    sample = json.loads((DATA_DIR / "frame_1.json").read_text())
    return {
        "workflow_id": "wf-capacity",
        "work_item": work_item,
        "command": "analyze_perception_result",
        "required_skill": "track_threat_situation_analysis",
        "payload": sample,
    }


def test_resource_and_algorithm_catalog_are_discoverable():
    resources = main.resources()
    algorithms = main.algorithms()
    card = main.agent_card()

    assert resources["agent"] == "track-threat-group-agent"
    assert resources["role"] == "track_threat"
    assert "system" in resources
    assert "process" in resources
    assert card["resourcesEndpoint"] == "/resources"
    assert card["algorithmsEndpoint"] == "/algorithms"
    assert card["recoveryEndpoint"] == "/recovery/notify"
    assert card["algorithmExecution"]["location"] == "agent_process"
    assert card["algorithmExecution"]["remote_execution"] is False

    assert algorithms["execution_location"] == "agent_process"
    assert algorithms["network_algorithm_calls"] is False
    assert algorithms["contract_version"] == "track_threat_algorithms/v1"
    assert any(item["algorithm_id"] == "covariance_kalman_cv_filter" for item in algorithms["algorithms"])
    assert any(item["backend"] == "torchscript" for item in algorithms["algorithms"])


@pytest.mark.anyio
async def test_send_message_rejects_when_stateful_capacity_is_full():
    main.reset_runtime_state()
    accepted = main.runtime.try_mark_busy("wf-existing", "wi-existing")
    assert accepted is True
    try:
        response = await main.send_message(_task("wi-capacity-rejected"), token="unit-test")
    finally:
        main.runtime.mark_idle()
        main.registrar.set_agent_status("idle")

    assert response["status"] == "failed"
    assert response["error_code"] == "AGENT_RESOURCE_EXHAUSTED"
    assert main.runtime.snapshot()["rejected_task_count"] == 1


def test_recovery_notification_restores_ready_and_can_clear_workflow_cache():
    runtime = A2ARuntimeState(agent_name="agent-a", role="track_threat")
    runtime.set_task_response(
        "wi-old",
        {"workflow_id": "wf-old", "work_item": "wi-old", "status": "completed"},
    )
    runtime.set_ready(False)

    acknowledgment = runtime.notify_recovery(
        {
            "workflow_id": "wf-old",
            "action": "resume",
            "reason": "commander_replanned",
            "reset_cache": True,
        }
    )

    assert acknowledgment["acknowledged"] is True
    assert acknowledgment["ready"] is True
    assert runtime.get_task_response("wi-old") is None
    assert runtime.recovery_notices()[-1]["reason"] == "commander_replanned"


def test_nacos_sdk_heartbeat_failure_uses_http_fallback(monkeypatch):
    settings = NacosSettings(enabled=True)
    registrar = NacosRegistrar(settings)
    calls: list[str] = []

    class FailingClient:
        def send_heartbeat(self, *args, **kwargs):
            raise RuntimeError("sdk heartbeat failed")

    registrar.client = FailingClient()
    monkeypatch.setattr(registrar, "_build_heartbeat_metadata", lambda: settings.metadata)
    monkeypatch.setattr(registrar, "_send_heartbeat_http", lambda: calls.append("http_beat"))
    monkeypatch.setattr(registrar, "_update_instance_metadata_http", lambda: calls.append("metadata"))

    registrar._send_heartbeat()

    assert calls == ["http_beat", "metadata"]
    status = registrar.status()
    assert status["heartbeat_success_count"] == 1
    assert status["heartbeat_failure_count"] == 0
    assert status["last_heartbeat_transport"] == "http"


def test_nacos_metadata_put_failure_falls_back_to_idempotent_registration(monkeypatch):
    settings = NacosSettings(enabled=True)
    registrar = NacosRegistrar(settings)
    requests: list[str] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"ok"

    def fake_urlopen(req, timeout=0):
        method = req.get_method()
        requests.append(method)
        if method == "PUT":
            raise RuntimeError("raft metadata group unavailable")
        return Response()

    monkeypatch.setattr("app.nacos_register.request.urlopen", fake_urlopen)

    registrar._update_instance_metadata_http()

    assert requests == ["PUT", "POST"]


def test_nacos_metadata_advertises_runtime_compatibility_endpoints():
    metadata = NacosSettings.from_env().metadata

    assert metadata["resources_endpoint"].endswith("/resources")
    assert metadata["recovery_endpoint"].endswith("/recovery/notify")
    assert metadata["algorithms_endpoint"].endswith("/algorithms")
    assert metadata["algorithm_loading_mode"] == "agent_local_model_bundle"
    assert metadata["remote_algorithm_execution"] == "false"


def test_nacos_sdk_and_http_paths_use_the_same_default_cluster(monkeypatch):
    settings = NacosSettings(enabled=True)
    registrar = NacosRegistrar(settings)
    calls: dict[str, dict] = {}

    class Client:
        def add_naming_instance(self, *args, **kwargs):
            calls["register"] = kwargs

        def send_heartbeat(self, *args, **kwargs):
            calls["heartbeat"] = kwargs

    registrar.client = Client()
    monkeypatch.setattr(registrar, "_build_heartbeat_metadata", lambda: settings.metadata)
    monkeypatch.setattr(registrar, "_update_instance_metadata_http", lambda: None)

    registrar._register_instance()
    registrar._send_heartbeat()

    assert calls["register"]["cluster_name"] == "DEFAULT"
    assert calls["heartbeat"]["cluster_name"] == "DEFAULT"


@pytest.mark.anyio
async def test_a2a_response_reports_selected_local_algorithms_and_stage_timings():
    main.reset_runtime_state()

    response = await main.send_message(_task("wi-algorithm-trace"), token="unit-test")

    assert response["status"] == "completed"
    assert "covariance_kalman_cv_filter" in response["selected_algorithms"]
    assert "st_gnn_dynamic_entity_tracking" in response["selected_algorithms"]
    assert response["algorithm_duration_ms"]["trajectory_tracking_and_prediction"] >= 0
    assert response["algorithm_duration_ms"]["threat_assessment_and_xai"] >= 0
    assert response["artifact"]["trace"]["algorithm_duration_ms"] == response["algorithm_duration_ms"]

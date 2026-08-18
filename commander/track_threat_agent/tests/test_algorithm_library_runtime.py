from __future__ import annotations

from dataclasses import replace

import pytest

from app.algorithm_library_runtime import (
    AlgorithmLibraryCall,
    AlgorithmLibraryClient,
    AlgorithmLibraryError,
    AlgorithmLibrarySettings,
    TrackThreatAlgorithmRuntime,
)
from app.tool_llm import AzureToolLLM, ToolLLMError


ACTIVE_ALGORITHMS = [
    {
        "algorithm_id": algorithm_id,
        "version": "1.0.0",
        "backend_type": "python_http_service",
        "owner_scope": "track_threat_agent",
    }
    for algorithm_id in (
        "multimodal_feature_fuser",
        "target_type_classifier",
        "track_state_updater",
        "trajectory_predictor",
        "graph_relation_reasoner",
    )
]


class FakeAlgorithmLibraryClient:
    def __init__(self) -> None:
        self.calls: list[AlgorithmLibraryCall] = []

    def list_algorithms(self) -> list[dict]:
        return list(ACTIVE_ALGORITHMS)

    def run_algorithm(self, *, request_id: str, trace_id: str, call: AlgorithmLibraryCall) -> dict:
        self.calls.append(call)
        return {
            "ok": True,
            "request_id": request_id,
            "trace_id": trace_id,
            "algorithm_id": call.algorithm_id,
            "version": call.version,
            "outputs": {"echo": call.inputs},
            "usage": {"latency_ms": 3.5},
            "error": None,
        }


class FakeToolLLM:
    def __init__(self, payload: dict | Exception) -> None:
        self.payload = payload
        self.last_kwargs: dict = {}

    def plan(self, **kwargs) -> dict:
        self.last_kwargs = kwargs
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _settings(**overrides) -> AlgorithmLibrarySettings:
    settings = AlgorithmLibrarySettings(
        enabled=True,
        required=False,
        base_url="http://127.0.0.1:8088",
        timeout_seconds=3.0,
        llm_enabled=True,
        llm_required=False,
        llm_provider="azure_openai",
        llm_endpoint="https://example.openai.azure.com",
        llm_deployment="gpt-4o-mini",
        llm_api_version="2024-12-01-preview",
        llm_api_key="unit-test",
        llm_timeout_seconds=5.0,
    )
    return replace(settings, **overrides)


def test_llm_plan_is_restricted_to_active_track_threat_algorithms():
    client = FakeAlgorithmLibraryClient()
    llm = FakeToolLLM(
        {
            "intent": "predict trajectories",
            "algorithm_calls": [
                {
                    "algorithm_id": "trajectory_predictor",
                    "version": "1.0.0",
                    "backend_type": "python_http_service",
                    "reason": "trajectory prediction requested",
                }
            ],
            "explanation": "use the forecasting algorithm",
        }
    )
    runtime = TrackThreatAlgorithmRuntime(_settings(), client=client, llm=llm)

    plan = runtime.begin_request(
        request_id="req-001",
        requested_skills=["trajectory_prediction"],
        request_summary={"detection_count": 3},
    )

    assert plan[0].algorithm_id == "trajectory_predictor"
    assert runtime.status()["planner_mode"] == "azure_openai_gpt_4o_mini"
    assert {
        item["algorithm_id"] for item in llm.last_kwargs["algorithms"]
    } == {"track_state_updater", "trajectory_predictor"}


def test_composite_skill_plan_is_completed_with_mandatory_algorithms():
    runtime = TrackThreatAlgorithmRuntime(
        _settings(),
        client=FakeAlgorithmLibraryClient(),
        llm=FakeToolLLM(
            {
                "intent": "analyze the full situation",
                "algorithm_calls": [
                    {
                        "algorithm_id": "target_type_classifier",
                        "version": "1.0.0",
                        "backend_type": "python_http_service",
                    },
                    {
                        "algorithm_id": "multimodal_feature_fuser",
                        "version": "1.0.0",
                        "backend_type": "python_http_service",
                    },
                    {
                        "algorithm_id": "graph_relation_reasoner",
                        "version": "1.0.0",
                        "backend_type": "python_http_service",
                    },
                ],
            }
        ),
    )

    plan = runtime.begin_request(
        request_id="req-composite",
        requested_skills=["track_threat_situation_analysis"],
        request_summary={"detection_count": 7},
    )

    assert [call.algorithm_id for call in plan] == [
        "multimodal_feature_fuser",
        "target_type_classifier",
        "track_state_updater",
        "trajectory_predictor",
        "graph_relation_reasoner",
    ]
    assert runtime.execution_trace()["planner_augmented_algorithms"] == [
        "track_state_updater",
        "trajectory_predictor",
    ]


def test_llm_cannot_select_an_algorithm_outside_the_track_threat_allowlist():
    runtime = TrackThreatAlgorithmRuntime(
        _settings(),
        client=FakeAlgorithmLibraryClient(),
        llm=FakeToolLLM(
            {
                "algorithm_calls": [
                    {
                        "algorithm_id": "decision_planning_core",
                        "version": "1.0.0",
                        "backend_type": "python_http_service",
                    }
                ]
            }
        ),
    )

    plan = runtime.begin_request(
        request_id="req-002",
        requested_skills=["trajectory_prediction"],
        request_summary={},
    )

    assert [call.algorithm_id for call in plan] == ["trajectory_predictor"]
    assert "not allowed" in runtime.execution_trace()["planner_fallback_reason"]


def test_llm_failure_uses_deterministic_skill_mapping_when_not_required():
    runtime = TrackThreatAlgorithmRuntime(
        _settings(),
        client=FakeAlgorithmLibraryClient(),
        llm=FakeToolLLM(ToolLLMError("azure unavailable")),
    )

    plan = runtime.begin_request(
        request_id="req-003",
        requested_skills=["trajectory_tracking", "trajectory_prediction"],
        request_summary={},
    )

    assert [call.algorithm_id for call in plan] == [
        "track_state_updater",
        "trajectory_predictor",
    ]
    assert runtime.status()["planner_mode"] == "deterministic_fallback"


def test_algorithm_library_client_posts_lzh_compatible_run_contract():
    captured: dict = {}

    def transport(method: str, url: str, payload: dict | None, _timeout: float) -> dict:
        captured.update(method=method, url=url, payload=payload)
        return {
            "ok": True,
            "algorithm_id": "trajectory_predictor",
            "version": "1.0.0",
            "outputs": {"predictions": []},
            "usage": {"latency_ms": 2.0},
        }

    client = AlgorithmLibraryClient(_settings(), transport=transport)
    client.run_algorithm(
        request_id="req-004",
        trace_id="trace-004",
        call=AlgorithmLibraryCall(
            algorithm_id="trajectory_predictor",
            version="1.0.0",
            backend_type="python_http_service",
            inputs={"tracks": [{"track_id": "trk-1"}]},
            params={"horizons_s": [10, 20, 30, 60]},
        ),
    )

    assert captured == {
        "method": "POST",
        "url": "http://127.0.0.1:8088/run",
        "payload": {
            "request_id": "req-004",
            "trace_id": "trace-004",
            "algorithm_id": "trajectory_predictor",
            "version": "1.0.0",
            "backend_type": "python_http_service",
            "inputs": {"tracks": [{"track_id": "trk-1"}]},
            "params": {"horizons_s": [10, 20, 30, 60]},
        },
    }


def test_algorithm_library_result_errors_are_not_treated_as_success():
    client = FakeAlgorithmLibraryClient()
    runtime = TrackThreatAlgorithmRuntime(
        _settings(llm_enabled=False),
        client=client,
        llm=None,
    )
    runtime.begin_request(
        request_id="req-005",
        requested_skills=["trajectory_prediction"],
        request_summary={},
    )

    class FailedClient(FakeAlgorithmLibraryClient):
        def run_algorithm(self, **_kwargs) -> dict:
            return {
                "ok": False,
                "error": {"code": "MODEL_ERROR", "message": "failed"},
            }

    runtime.client = FailedClient()
    with pytest.raises(AlgorithmLibraryError, match="MODEL_ERROR"):
        runtime.run(
            "trajectory_predictor",
            inputs={"tracks": [{"track_id": "trk-1"}]},
            params={},
        )


def test_azure_tool_llm_uses_chat_deployment_not_embedding_deployment():
    captured: dict = {}

    def transport(url: str, headers: dict, payload: dict, _timeout: float) -> dict:
        captured.update(url=url, headers=headers, payload=payload)
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"intent":"test","algorithm_calls":[],"explanation":"ok"}'
                    }
                }
            ]
        }

    llm = AzureToolLLM(_settings(), transport=transport)
    result = llm.plan(
        requested_skills=["trajectory_prediction"],
        algorithms=ACTIVE_ALGORITHMS,
        request_summary={"detection_count": 1},
    )

    assert result["intent"] == "test"
    assert "/deployments/gpt-4o-mini/chat/completions" in captured["url"]
    assert captured["headers"]["api-key"] == "unit-test"
    assert captured["payload"]["response_format"] == {"type": "json_object"}

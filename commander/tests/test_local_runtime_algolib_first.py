from local_runtime import LocalAgentRuntime


class FakeAlgorithmLibraryClient:
    def list_algorithms(self, *, active_only=True):
        return [
            {
                "algorithm_id": "battlefield_rtdetr_detector",
                "version": "1.0.0",
                "backend_type": "python_http_service",
            },
            {
                "algorithm_id": "edl_evidential_verifier",
                "version": "1.0.0",
                "backend_type": "python_http_service",
            },
            {
                "algorithm_id": "motr_neural_kalman_tracker",
                "version": "1.0.0",
                "backend_type": "python_http_service",
            },
            {
                "algorithm_id": "multimodal_mamba_fusion",
                "version": "1.0.0",
                "backend_type": "python_http_service",
            },
            {
                "algorithm_id": "supcon_meta_classifier",
                "version": "1.0.0",
                "backend_type": "python_http_service",
            },
        ]

    def predict(self, algorithm_id, inputs, **_kwargs):
        if algorithm_id == "battlefield_rtdetr_detector":
            return {"detections": [{"id": "D-1"}], "count": 1}
        if algorithm_id == "edl_evidential_verifier":
            return {"verified_detections": [{"id": "D-1"}], "count": 1}
        if algorithm_id == "motr_neural_kalman_tracker":
            return {"tracks": [{"track_id": "T-1"}], "count": 1}
        if algorithm_id == "multimodal_mamba_fusion":
            return {"fused_embeddings": {"T-1": [0.1]}, "count": 1}
        if algorithm_id == "supcon_meta_classifier":
            return {"classifications": [{"track_id": "T-1", "class": "hostile"}], "count": 1}
        raise AssertionError(algorithm_id)


def test_tactical_intelligence_prefers_algolib_and_reports_algorithm_calls(monkeypatch):
    monkeypatch.setattr("local_runtime.AlgorithmLibraryClient", FakeAlgorithmLibraryClient)
    runtime = LocalAgentRuntime()

    response, _events = runtime.execute(
        "tactical_intelligence",
        {
            "schema_version": "1.0",
            "workflow_id": "wf-algolib",
            "work_item": "wf-algolib:tia",
            "command": "buildSituationSummary",
            "required_skill": "tactical_intelligence_analysis",
            "input": {"mission_input": {"contacts": [{"contact_id": "T-1"}]}},
            "output_hint": "cognition_result",
        },
        stream=False,
    )

    result = response["output"]["cognition_result"]
    assert result["execution_mode"] == "local_agent_with_algolib_runtime"
    assert [row["algorithm_id"] for row in result["algorithm_calls"]] == [
        "battlefield_rtdetr_detector",
        "edl_evidential_verifier",
        "motr_neural_kalman_tracker",
        "multimodal_mamba_fusion",
        "supcon_meta_classifier",
    ]
    assert all(row["execution_mode"] == "algolib_runtime" for row in result["algorithm_calls"])


def test_track_threat_prefers_matching_algolib_algorithm(monkeypatch):
    monkeypatch.setattr("local_runtime.AlgorithmLibraryClient", FakeAlgorithmLibraryClient)
    runtime = LocalAgentRuntime()

    response, _events = runtime.execute(
        "track_threat",
        {
            "schema_version": "1.0",
            "workflow_id": "wf-algolib",
            "work_item": "wf-algolib:track",
            "command": "updateTracks",
            "required_skill": "trajectory_tracking",
            "input": {
                "cognition_result": {
                    "value": {"targets": [{"track_id": "T-1", "threat_score": 0.8}]}
                }
            },
            "output_hint": "tracking_result",
        },
        stream=False,
    )

    result = response["output"]["tracking_result"]
    assert result["selected_algorithms"] == ["motr_neural_kalman_tracker"]
    assert result["algorithm_calls"][0]["execution_mode"] == "algolib_runtime"

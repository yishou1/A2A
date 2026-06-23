import json
from pathlib import Path

import pytest

from app import main
from app.algorithm_provider import PlanAlgorithmProvider


DATA_DIR = Path(__file__).resolve().parents[1] / "sample_data"


def test_default_algorithm_provider_uses_project_plan_contract():
    provider = main.algorithm_provider

    assert isinstance(provider, PlanAlgorithmProvider)
    assert provider.mode == "plan_algorithm_provider"

    contract = provider.algorithm_contract()
    assert contract["primary_algorithms"]["trajectory_prediction"] == "st_gnn_dynamic_entity_tracking"
    assert contract["primary_algorithms"]["threat_assessment"] == "dynamic_bayesian_network"
    assert contract["primary_algorithms"]["semantic_reasoning"] == "kg_transformer_semantic_sitrep"
    assert contract["primary_algorithms"]["explainability"] == "xai_evidence_chain"
    assert contract["fallback_providers"]["trajectory_prediction"] == "baseline_motion_provider"


@pytest.mark.anyio
async def test_artifact_exposes_plan_algorithm_trace_for_reporting():
    await main.demo_reset()
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

    assert summary["algorithm_provider"]["mode"] == "plan_algorithm_provider"
    assert summary["algorithm_provider"]["primary_algorithms"]["trajectory_prediction"] == "st_gnn_dynamic_entity_tracking"
    assert summary["algorithm_provider"]["fallback_providers"]["trajectory_prediction"] == "baseline_motion_provider"

    first_track = artifact["tracks"][0]
    first_prediction = first_track["predicted_path"][0]
    first_threat = artifact["threats"][0]

    assert first_track["metadata"]["plan_algorithms"]["trajectory_prediction"]["algorithm"] == "ST-GNN"
    assert first_prediction["st_gnn"]["algorithm"] == "ST-GNN"
    assert first_prediction["st_gnn"]["fallback_provider"] == "baseline_motion_provider"
    assert first_threat["metadata"]["plan_algorithms"]["threat_assessment"]["algorithm"] == "DBN"
    assert first_threat["metadata"]["plan_algorithms"]["semantic_reasoning"]["algorithm"] == "KG+Transformer"
    assert first_threat["metadata"]["xai"]["algorithm"] == "XAI"

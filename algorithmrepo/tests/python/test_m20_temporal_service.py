from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import jsonschema
import numpy as np
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "services"), str(ROOT)]

from a2a_algorithms_common import track_threat_algorithms as algorithms
from a2a_algorithms_common.m20_fused_onnx import M20FusedOnnxBundle, temporal_history
from track_threat_algorithms.app.main import app

PACKAGE = ROOT / "examples/graph_relation_reasoner/2.0.0"


def request() -> dict:
    return json.loads((PACKAGE / "golden_cases/case_001_request.json").read_text())


def test_real_temporal_http_request_matches_both_schemas():
    payload = request()
    jsonschema.validate(payload["inputs"], json.loads((PACKAGE / "input.schema.json").read_text()))
    client = TestClient(app)
    health = client.get("/graph_relation_reasoner/health")
    assert health.status_code == 200 and health.json()["model_loaded"]
    assert health.json()["version"] == "2.0.0"
    response = client.post("/graph_relation_reasoner/predict", json=payload)
    assert response.status_code == 200 and response.json()["ok"]
    outputs = response.json()["outputs"]
    jsonschema.validate(outputs, json.loads((PACKAGE / "output.schema.json").read_text()))
    assert outputs["model"]["model_family"] == "type_name_routed_temporal_gru_gnn_onnx"
    assert outputs["groups"] and outputs["relations"]
    # Empty params must select GRU too, not silently fall back to the old GNN.
    payload["params"] = {}
    assert client.post("/graph_relation_reasoner/predict", json=payload).json()["outputs"]["model"]["model_version"] == "2.0.0"
    payload["version"] = "1.0.0"
    assert client.post("/graph_relation_reasoner/predict", json=payload).status_code == 404


@pytest.mark.parametrize("type_name,domain", [("fighter", "aircraft"), ("uav", "uav"), ("ship", "ship"), ("truck", "ground_vehicle")])
def test_each_real_expert_executes_and_uses_temporal_inputs(type_name, domain):
    bundle = algorithms._load_m20_fused_onnx_bundle()
    assert bundle.route(type_name)["domain"] == domain
    tracks = request()["inputs"]["tracks"]
    probabilities, selected = bundle.score(domain, tracks)
    assert len(selected) == 2 and probabilities.shape == (1, 10, 10)
    assert np.isfinite(probabilities).all()
    inputs = {item.name: item.shape for item in bundle.experts[domain].get_inputs()}
    assert inputs["node_sequences"][1:] == [8, 10, 7]
    assert inputs["edge_inputs"][1:] == [10, 10, 12]
    assert "node_time_mask" in inputs


@pytest.mark.parametrize("type_name", ["radar_site", "zzzzzzzz987654", "unknown"])
def test_unsupported_types_are_explicitly_declined(type_name):
    payload = request()
    for track in payload["inputs"]["tracks"]:
        track["type_name"] = type_name
    outputs = algorithms.graph_relation_reasoner(payload["inputs"], {})
    assert not outputs["groups"] and not outputs["relations"]
    assert all(row["domain"] is None and row["reason"] for row in outputs["routing"])


def test_insufficient_history_and_expert_capacity_are_not_silent_truncation():
    payload = request()
    payload["inputs"]["tracks"][0]["history_path"] = []
    outputs = algorithms.graph_relation_reasoner(payload["inputs"], {})
    assert outputs["routing"][0]["reason"] == "insufficient_history_points"
    base = request()["inputs"]["tracks"][0]
    tracks = [{**copy.deepcopy(base), "track_id": str(i)} for i in range(11)]
    outputs = algorithms.graph_relation_reasoner({"tracks": tracks}, {})
    assert outputs["routing"][-1]["reason"] == "expert_node_capacity_exceeded"
    assert outputs["graph_summary"]["routed_node_count"] == 10


@pytest.mark.parametrize("change", [
    lambda p: p.update(inputs={}),
    lambda p: p["inputs"]["tracks"][0].pop("lat"),
    lambda p: p["inputs"]["tracks"][0].update(speed="fast"),
    lambda p: p["inputs"]["tracks"][0].update(lat=100),
    lambda p: p["inputs"]["tracks"][0].update(history_path=[{}]),
    lambda p: p["params"].update(relation_threshold=1),
    lambda p: p["params"].update(model_variant="single_frame"),
])
def test_invalid_direct_http_input_returns_explicit_business_error(change):
    payload = request()
    change(payload)
    response = TestClient(app).post("/graph_relation_reasoner/predict", json=payload)
    assert response.status_code == 200  # Existing service envelope contract.
    assert response.json()["ok"] is False
    assert response.json()["error"]["code"] == "ValueError"


def test_readiness_checks_actual_temporal_bundle_and_does_not_use_legacy(monkeypatch):
    def unavailable():
        raise FileNotFoundError("temporal bundle unavailable")
    monkeypatch.setattr(algorithms, "_load_m20_fused_onnx_bundle", unavailable)
    client = TestClient(app)
    assert client.get("/graph_relation_reasoner/health").json()["model_loaded"] is False
    response = client.post("/graph_relation_reasoner/predict", json=request())
    assert response.status_code == 503


def test_corrupt_artifact_is_rejected_before_loading(tmp_path):
    root = PACKAGE / "model_bundle"
    manifest = json.loads((root / "manifest.json").read_text())
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    router = tmp_path / "type_name_router"
    router.mkdir()
    (router / "manifest.json").write_bytes((root / "type_name_router/manifest.json").read_bytes())
    (router / "model.onnx").write_bytes(b"not-the-original-model")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        M20FusedOnnxBundle(tmp_path)


def test_stationary_observations_are_not_mistaken_for_one_frame():
    track = {**request()["inputs"]["tracks"][0], "timestamp": 6}
    track["history_path"] = [{"lat": track["lat"], "lon": track["lon"], "timestamp": i} for i in range(6)]
    assert len(temporal_history(track)) == 7


def test_startup_selects_only_latest_m20_and_both_trees_have_identical_artifacts():
    project = ROOT.parent
    spec = importlib.util.spec_from_file_location("algorithm_packages", project / "scripts/algorithm_packages.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for tree in ("algorithmrepo", "commander"):
        rows = [row for row in module.algorithm_card_rows(project / tree / "examples")
                if row[1] == "graph_relation_reasoner" and row[3] == "python_http_service"]
        assert len(rows) == 1 and rows[0][2] == "2.0.0"
        assert rows[0][-1].endswith("/graph_relation_reasoner/health")
    for artifact in (PACKAGE / "model_bundle").rglob("*.onnx"):
        counterpart = project / "commander" / artifact.relative_to(ROOT)
        assert hashlib.sha256(artifact.read_bytes()).digest() == hashlib.sha256(counterpart.read_bytes()).digest()

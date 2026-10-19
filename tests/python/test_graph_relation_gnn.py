from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from safetensors.torch import load_file


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "services"), str(ROOT)]

from agent.inference.models.graph_relation_gnn import DenseGraphRelationGNN  # noqa: E402
from a2a_algorithms_common import track_threat_algorithms  # noqa: E402
from scripts.train_graph_relation_gnn import evaluate  # noqa: E402


CHECKPOINT = ROOT / "models/checkpoints/graph_relation_gnn_s.safetensors"
METADATA = ROOT / "models/checkpoints/graph_relation_gnn_s.metadata.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_artifact_dataset_and_training_hashes_are_verified() -> None:
    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    assert metadata["model_family"] == "dense_message_passing_graph_neural_network"
    assert metadata["architecture"]["parameter_count"] == 16673
    assert _sha256(CHECKPOINT) == metadata["artifact_sha256"]
    assert _sha256(ROOT / metadata["training_script"]) == metadata[
        "training_script_sha256"
    ]
    for artifact in metadata["dataset"]["artifacts"].values():
        assert _sha256(ROOT / artifact["path"]) == artifact["sha256"]


def test_checkpoint_reproduces_holdout_metrics_and_beats_rule_baseline() -> None:
    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    artifacts = metadata["dataset"]["artifacts"]
    values = {
        name: np.load(ROOT / artifact["path"], allow_pickle=False)
        for name, artifact in artifacts.items()
    }
    model = DenseGraphRelationGNN()
    model.load_state_dict(load_file(str(CHECKPOINT), device="cpu"), strict=True)
    model.eval()
    actual = evaluate(
        model,
        values["holdout_nodes"],
        values["holdout_edges"],
        values["holdout_masks"],
        values["holdout_labels"],
    )
    expected = {
        key: value for key, value in metadata["evaluation"].items() if key != "method"
    }
    assert actual == expected
    assert actual["trained_gnn"]["f1"] > actual[
        "distance_motion_rule_baseline"
    ]["f1"]


def test_runtime_uses_checkpoint_and_rejects_close_motion_mismatch() -> None:
    outputs = track_threat_algorithms.graph_relation_reasoner(
        {
            "tracks": [
                {
                    "track_id": "A",
                    "object_type": "aircraft",
                    "lat": 31.0,
                    "lon": 121.0,
                    "speed": 230,
                    "heading": 86,
                    "alt": 8000,
                    "confidence": 0.9,
                },
                {
                    "track_id": "B",
                    "object_type": "ship",
                    "lat": 31.001,
                    "lon": 121.001,
                    "speed": 80,
                    "heading": 170,
                    "alt": 0,
                    "confidence": 0.9,
                },
            ]
        },
        {},
    )
    assert outputs["relations"] == []
    assert outputs["groups"] == []
    assert outputs["model"]["artifact_sha256"] == _sha256(CHECKPOINT)
    assert outputs["model"]["model_family"] == "dense_message_passing_graph_neural_network"


def test_runtime_does_not_fall_back_when_model_load_fails(monkeypatch) -> None:
    def fail_load():
        raise FileNotFoundError("checkpoint unavailable")

    monkeypatch.setattr(track_threat_algorithms, "_load_graph_relation_model", fail_load)
    try:
        track_threat_algorithms.graph_relation_reasoner(
            {
                "tracks": [
                    {"track_id": "A", "lat": 31.0, "lon": 121.0, "speed": 100, "heading": 0},
                    {"track_id": "B", "lat": 31.1, "lon": 121.1, "speed": 100, "heading": 0},
                ]
            },
            {},
        )
    except FileNotFoundError as exc:
        assert "checkpoint unavailable" in str(exc)
    else:
        raise AssertionError("missing GNN checkpoint must not use a rule fallback")

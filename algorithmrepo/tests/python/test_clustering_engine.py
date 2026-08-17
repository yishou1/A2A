from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "services"
sys.path.insert(0, str(SERVICES))

from a2a_algorithms_common.clustering import cluster_points  # noqa: E402
from clustering_engine.app.main import app  # noqa: E402


def test_kmeans_separates_two_groups_deterministically() -> None:
    inputs = {"points": [[0, 0], [0, 2], [10, 10], [10, 12]]}
    params = {"algorithm": "kmeans", "k": 2}

    first = cluster_points(inputs, params)
    second = cluster_points(inputs, params)

    assert first == second
    assert first["labels"] == [0, 0, 1, 1]
    assert [cluster["centroid"] for cluster in first["clusters"]] == [
        [0.0, 1.0],
        [10.0, 11.0],
    ]
    assert first["inertia"] == 4.0
    assert first["converged"] is True


def test_dbscan_finds_density_groups_and_noise() -> None:
    result = cluster_points(
        {"points": [[0, 0], [0.1, 0], [5, 5], [5.1, 5], [10, 10]]},
        {"algorithm": "dbscan", "eps": 0.2, "min_samples": 2},
    )

    assert result["labels"] == [0, 0, 1, 1, -1]
    assert result["cluster_count"] == 2
    assert result["noise_indices"] == [4]
    assert result["clusters"][1]["centroid"] == [5.05, 5.0]


@pytest.mark.parametrize(
    ("inputs", "params", "message"),
    [
        ({"points": [[0, 0]]}, {"algorithm": "kmeans"}, "at least two"),
        (
            {"points": [[0, 0], [1]]},
            {"algorithm": "kmeans"},
            "same dimension",
        ),
        (
            {"points": [[0, 0], [1, 1]]},
            {"algorithm": "kmeans", "k": 3},
            "between 1",
        ),
        (
            {"points": [[0, 0], [1, 1]]},
            {"algorithm": "dbscan", "eps": 0},
            "positive",
        ),
    ],
)
def test_invalid_inputs_are_rejected(inputs: dict, params: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        cluster_points(inputs, params)


def test_http_identity_and_both_golden_cases() -> None:
    client = TestClient(app)
    health = client.get("/health").json()
    assert health["ok"] is True
    assert health["model_loaded"] is True

    metadata = client.get("/metadata").json()
    assert metadata["algorithm_id"] == "clustering_engine"
    assert metadata["algorithm_class"] == "M01"
    assert metadata["implementations"] == ["kmeans", "dbscan"]

    golden_dir = ROOT / "examples" / "clustering_engine" / "1.0.0" / "golden_cases"
    for request_path in sorted(golden_dir.glob("*_request.json")):
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        response = client.post("/predict", json=payload).json()
        assert response["ok"] is True
        assert response["outputs"]["model_runtime"]["used"] is True


def test_http_returns_structured_error_for_bad_algorithm() -> None:
    client = TestClient(app)
    response = client.post(
        "/predict",
        json={
            "algorithm_id": "clustering_engine",
            "version": "1.0.0",
            "inputs": {"points": [[0, 0], [1, 1]]},
            "params": {"algorithm": "unknown"},
        },
    ).json()
    assert response["ok"] is False
    assert response["error"]["code"] == "ValueError"


def test_implementation_provenance_hash_matches_source() -> None:
    package = ROOT / "examples" / "clustering_engine" / "1.0.0"
    metadata = json.loads(
        (package / "implementation.metadata.json").read_text(encoding="utf-8")
    )
    source = ROOT / metadata["source_path"]
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    assert metadata["artifact_type"] == "source_code"
    assert metadata["training_required"] is False
    assert digest == metadata["sha256"]

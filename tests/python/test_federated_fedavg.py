from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from a2a_algorithms_common.federated_fedavg import (  # noqa: E402
    federated_average,
    implementation_loaded,
    load_reference_metadata,
)
from federated_fedavg_aggregator.app.main import app  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _golden_payload() -> dict:
    path = (
        ROOT
        / "examples"
        / "federated_fedavg_aggregator"
        / "1.0.0"
        / "golden_cases"
        / "case_001_request.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_fedavg_uses_client_sample_counts_for_nested_tensors() -> None:
    payload = _golden_payload()
    result = federated_average(payload["inputs"], payload["params"])

    assert result["aggregated_weights"] == {
        "bias": [2.0, 0.0],
        "layer": [[2.0, 3.0], [4.0, 5.0]],
    }
    assert result["total_samples"] == 3
    assert [item["aggregation_weight"] for item in result["participants"]] == [
        0.666666666667,
        0.333333333333,
    ]
    assert result["model_runtime"]["used"] is True


def test_reference_experiment_artifacts_and_metrics_are_verifiable() -> None:
    metadata = load_reference_metadata()
    implementation = ROOT / metadata["implementation"]
    dataset = ROOT / metadata["dataset"]["path"]
    weights = ROOT / metadata["final_global_weights"]["path"]
    script = ROOT / metadata["experiment_script"]

    assert implementation_loaded() is True
    assert _normalized_text_sha256(implementation) == metadata["implementation_sha256"]
    assert _normalized_text_sha256(script) == metadata["experiment_script_sha256"]
    assert _sha256(dataset) == metadata["dataset"]["sha256"]
    assert _sha256(weights) == metadata["final_global_weights"]["sha256"]
    assert metadata["protocol"]["rounds"] == 8
    assert metadata["final_evaluation"]["sample_count"] == 360
    assert metadata["dataset"]["limitation"]


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        (
            [
                {"client_id": "a", "sample_count": 1, "weights": {"w": [1, 2]}},
                {"client_id": "b", "sample_count": 1, "weights": {"w": [1]}},
            ],
            "shape",
        ),
        (
            [
                {"client_id": "a", "sample_count": 1, "weights": {"w": [1]}},
                {"client_id": "b", "sample_count": 1, "weights": {"b": [1]}},
            ],
            "same weight tensor names",
        ),
        (
            [
                {"client_id": "a", "sample_count": 1, "weights": {"w": [1]}},
                {"client_id": "a", "sample_count": 1, "weights": {"w": [2]}},
            ],
            "duplicate client_id",
        ),
        (
            [
                {"client_id": "a", "sample_count": 1, "weights": {"w": [1]}},
                {"client_id": "b", "sample_count": 0, "weights": {"w": [2]}},
            ],
            "positive integer",
        ),
    ],
)
def test_invalid_client_updates_are_rejected(updates: list[dict], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        federated_average({"client_updates": updates}, {"minimum_clients": 2})


def test_minimum_client_policy_is_enforced() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        federated_average(
            {
                "client_updates": [
                    {"client_id": "a", "sample_count": 1, "weights": {"w": [1]}}
                ]
            },
            {"minimum_clients": 2},
        )


def test_http_health_metadata_and_golden_round() -> None:
    client = TestClient(app)
    health = client.get("/health").json()
    assert health["ok"] is True
    assert health["model_loaded"] is True

    metadata = client.get("/metadata").json()
    assert metadata["algorithm_class"] == "M12"
    assert metadata["aggregation_protocol"] == "fedavg_weighted_by_sample_count"

    response = client.post("/predict", json=_golden_payload()).json()
    assert response["ok"] is True
    assert response["outputs"]["participant_count"] == 2

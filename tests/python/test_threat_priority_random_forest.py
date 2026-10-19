from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from a2a_algorithms_common.threat_priority_random_forest import (  # noqa: E402
    load_metadata,
    model_loaded,
    predict_threat_priorities,
)
from threat_priority_random_forest.app.main import app  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _golden_payload() -> dict:
    path = (
        ROOT
        / "examples"
        / "threat_priority_random_forest"
        / "1.0.0"
        / "golden_cases"
        / "case_001_request.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_model_artifact_and_training_inputs_match_metadata_hashes() -> None:
    metadata = load_metadata()

    assert model_loaded() is True
    assert _sha256(ROOT / metadata["artifact_path"]) == metadata["artifact_sha256"]
    assert _sha256(ROOT / metadata["dataset"]["path"]) == metadata["dataset"]["sha256"]
    assert (
        _normalized_text_sha256(ROOT / metadata["training_script"])
        == metadata["training_script_sha256"]
    )
    assert metadata["evaluation"]["test_count"] > 0
    assert metadata["dataset"]["limitation"]


def test_random_forest_predicts_and_sorts_reference_examples() -> None:
    payload = _golden_payload()
    result = predict_threat_priorities(payload["inputs"], payload["params"])

    assert [item["priority"] for item in result["priorities"]] == [
        "high",
        "medium",
        "low",
    ]
    assert result["model_runtime"]["backend"] == "sklearn_random_forest"
    assert result["model_runtime"]["used"] is True
    assert all(
        abs(sum(item["class_probabilities"].values()) - 1.0) < 2e-6
        for item in result["priorities"]
    )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda target: target.pop("target_id"),
        lambda target: target.update(threat_score=1.5),
        lambda target: target.update(distance_km=float("nan")),
    ],
)
def test_invalid_target_is_rejected(mutator) -> None:
    target = dict(_golden_payload()["inputs"]["targets"][0])
    mutator(target)
    with pytest.raises(ValueError):
        predict_threat_priorities({"targets": [target]}, {})


def test_http_health_metadata_and_golden_prediction() -> None:
    client = TestClient(app)
    health = client.get("/health").json()
    assert health["ok"] is True
    assert health["model_loaded"] is True

    metadata = client.get("/metadata").json()
    assert metadata["algorithm_class"] == "M05"
    assert metadata["model_family"] == "sklearn_random_forest_classifier"

    response = client.post("/predict", json=_golden_payload()).json()
    assert response["ok"] is True
    assert response["outputs"]["target_count"] == 3

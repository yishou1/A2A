from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from a2a_algorithms_common.intent_gaussian_naive_bayes import (  # noqa: E402
    load_metadata,
    model_loaded,
    predict_intents,
)
from intent_gaussian_naive_bayes.app.main import app  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _golden_payload() -> dict:
    path = (
        ROOT
        / "examples"
        / "intent_gaussian_naive_bayes"
        / "1.0.0"
        / "golden_cases"
        / "case_001_request.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_model_dataset_and_training_script_match_metadata() -> None:
    metadata = load_metadata()
    assert model_loaded() is True
    assert _sha256(ROOT / metadata["artifact_path"]) == metadata["artifact_sha256"]
    assert _sha256(ROOT / metadata["dataset"]["path"]) == metadata["dataset"]["sha256"]
    assert (
        _normalized_text_sha256(ROOT / metadata["training_script"])
        == metadata["training_script_sha256"]
    )
    assert metadata["evaluation"]["test_count"] == 135
    assert metadata["dataset"]["limitation"]


def test_gaussian_nb_returns_expected_intents_and_posteriors() -> None:
    payload = _golden_payload()
    result = predict_intents(payload["inputs"], payload["params"])
    assert [item["intent"] for item in result["predictions"]] == [
        "benign",
        "surveillance",
        "hostile",
    ]
    assert result["model_runtime"]["used"] is True
    for item in result["predictions"]:
        assert abs(sum(item["posterior_probabilities"].values()) - 1.0) < 2e-6


@pytest.mark.parametrize(
    "mutator",
    [
        lambda observation: observation.pop("observation_id"),
        lambda observation: observation.update(radar_activity=-0.1),
        lambda observation: observation.update(proximity_score=float("inf")),
    ],
)
def test_invalid_observation_is_rejected(mutator) -> None:
    observation = dict(_golden_payload()["inputs"]["observations"][0])
    mutator(observation)
    with pytest.raises(ValueError):
        predict_intents({"observations": [observation]}, {})


def test_http_health_metadata_and_golden_prediction() -> None:
    client = TestClient(app)
    health = client.get("/health").json()
    assert health["ok"] is True
    assert health["model_loaded"] is True

    metadata = client.get("/metadata").json()
    assert metadata["algorithm_class"] == "M07"
    assert metadata["model_family"] == "sklearn_gaussian_naive_bayes"

    response = client.post("/predict", json=_golden_payload()).json()
    assert response["ok"] is True
    assert response["outputs"]["observation_count"] == 3

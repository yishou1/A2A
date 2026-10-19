from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from a2a_algorithms_common.conditional_tabular_gan import (  # noqa: E402
    generate_conditional_samples,
    load_metadata,
    model_loaded,
)
from conditional_tabular_gan.app.main import app  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _golden_payload() -> dict:
    path = (
        ROOT
        / "examples"
        / "conditional_tabular_gan"
        / "1.0.0"
        / "golden_cases"
        / "case_001_request.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_trained_generator_and_provenance_hashes_are_valid() -> None:
    metadata = load_metadata()
    assert model_loaded() is True
    assert _sha256(ROOT / metadata["artifact_path"]) == metadata["artifact_sha256"]
    assert _sha256(ROOT / metadata["dataset"]["path"]) == metadata["dataset"]["sha256"]
    assert (
        _normalized_text_sha256(ROOT / metadata["generator_source"])
        == metadata["generator_source_sha256"]
    )
    assert (
        _normalized_text_sha256(ROOT / metadata["training_script"])
        == metadata["training_script_sha256"]
    )
    assert metadata["training"]["epochs"] == 180
    assert metadata["evaluation"]["overall_mean_absolute_error"] < 0.1
    assert metadata["dataset"]["limitation"]


def test_seeded_generation_is_reproducible_and_bounded() -> None:
    payload = _golden_payload()
    first = generate_conditional_samples(payload["inputs"], payload["params"])
    second = generate_conditional_samples(payload["inputs"], payload["params"])
    different = generate_conditional_samples(payload["inputs"], {"seed": 43})

    assert first == second
    assert first["samples"] != different["samples"]
    assert first["data_status"] == "synthetic_generated_data"
    assert first["model_runtime"]["used"] is True
    assert all(0.0 <= value <= 1.0 for row in first["samples"] for value in row.values())


@pytest.mark.parametrize(
    ("inputs", "params"),
    [
        ({"condition": "unknown", "sample_count": 1}, {}),
        ({"condition": "benign", "sample_count": 0}, {}),
        ({"condition": "benign", "sample_count": 1}, {"seed": -1}),
        ({"condition": "benign", "sample_count": 1}, {"temperature": 0}),
    ],
)
def test_invalid_generation_request_is_rejected(inputs: dict, params: dict) -> None:
    with pytest.raises(ValueError):
        generate_conditional_samples(inputs, params)


def test_http_health_metadata_and_golden_generation() -> None:
    client = TestClient(app)
    health = client.get("/health").json()
    assert health["ok"] is True
    assert health["model_loaded"] is True

    metadata = client.get("/metadata").json()
    assert metadata["algorithm_class"] == "M08"
    assert metadata["model_family"] == "pytorch_conditional_gan"

    response = client.post("/predict", json=_golden_payload()).json()
    assert response["ok"] is True
    assert response["outputs"]["sample_count"] == 3
    assert response["outputs"]["data_status"] == "synthetic_generated_data"

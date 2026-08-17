from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
from safetensors.torch import load_file

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "services"), str(ROOT)]

from agent.inference.models.edl_head import EvidentialHead  # noqa: E402
from agent.inference.registry import clear_model_cache, get_edl_head  # noqa: E402
from a2a_algorithms_common import tia_predictors  # noqa: E402
from scripts.train_edl_evidential_verifier import evaluate  # noqa: E402

CHECKPOINT = ROOT / "models/checkpoints/edl_head_s.safetensors"
METADATA = ROOT / "models/checkpoints/edl_head_s.metadata.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def real_small_profile(monkeypatch):
    monkeypatch.setenv("TIA_USE_MOCK", "0")
    monkeypatch.setenv("TIA_COMPUTE_PROFILE", "small")
    tia_predictors._config.cache_clear()
    tia_predictors._backend.cache_clear()
    clear_model_cache()
    yield
    tia_predictors._config.cache_clear()
    tia_predictors._backend.cache_clear()
    clear_model_cache()


def test_artifact_dataset_and_training_hashes_are_verified() -> None:
    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    dataset = ROOT / metadata["dataset"]["path"]
    script = ROOT / metadata["training_script"]
    assert _sha256(CHECKPOINT) == metadata["artifact_sha256"]
    assert _sha256(dataset) == metadata["dataset"]["sha256"]
    assert _sha256(script) == metadata["training_script_sha256"]
    assert metadata["architecture"]["parameter_count"] == 290


def test_checkpoint_reproduces_calibration_metrics() -> None:
    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    records = json.loads((ROOT / metadata["dataset"]["path"]).read_text(encoding="utf-8"))
    model = EvidentialHead()
    model.load_state_dict(load_file(str(CHECKPOINT), device="cpu"), strict=True)
    model.eval()
    metrics = evaluate(model, records)
    assert metrics == {key: value for key, value in metadata["evaluation"].items() if key != "method"}
    assert metrics["brier_score"] < metrics["raw_detector_brier_baseline"]
    assert metrics["expected_calibration_error_10_bin"] < metrics["raw_detector_ece_baseline"]


def test_real_predictor_returns_partitioned_auditable_evidence() -> None:
    request = json.loads(
        (
            ROOT
            / "examples/edl_evidential_verifier/1.0.0/golden_cases/case_001_request.json"
        ).read_text(encoding="utf-8")
    )
    assert tia_predictors.tia_model_loaded("edl_evidential_verifier") is True
    output = tia_predictors.predict_edl_evidential_verifier(request["inputs"], {})
    assert output["summary"] == {"total": 2, "verified": 1, "rejected": 1, "manual_review": 0}
    assert output["count"] == 1
    assert output["model"]["artifact_sha256"] == _sha256(CHECKPOINT)
    for assessment in output["assessments"]:
        evidence = assessment["evidence"]
        assert set(evidence["class_evidence"]) == {"rejected", "verified"}
        assert sum(evidence["class_probabilities"].values()) == pytest.approx(1.0, abs=1e-5)
        assert len(evidence["decision_reasons"]) == 2


def test_ambiguous_detection_enters_human_review_queue() -> None:
    output = tia_predictors.predict_edl_evidential_verifier(
        {
            "detections": [
                {
                    "detection_id": "AMB-1",
                    "confidence": 0.60,
                    "bbox": [100, 100, 160, 180],
                    "damage_score": 0.0,
                }
            ]
        },
        {},
    )
    assert output["summary"]["manual_review"] == 1
    assert output["review_queue"][0]["decision"] == "manual_review"
    assert output["review_queue"][0]["verified"] is False


def test_registry_refuses_missing_checkpoint() -> None:
    with pytest.raises(FileNotFoundError, match="trained EDL checkpoint"):
        get_edl_head(
            {
                "compute_profile": "small",
                "edl_checkpoint": "definitely_missing_edl.safetensors",
                "device": "cpu",
            }
        )

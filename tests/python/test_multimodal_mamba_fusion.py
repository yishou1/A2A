from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest
from safetensors.torch import load_file

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "services"), str(ROOT)]

from agent.inference.models.mamba_fusion import MultimodalMambaBlock  # noqa: E402
from agent.inference.registry import clear_model_cache, get_mamba_fusion  # noqa: E402
from a2a_algorithms_common import tia_predictors  # noqa: E402
from scripts.train_multimodal_mamba_fusion import evaluate  # noqa: E402

CHECKPOINT = ROOT / "models/checkpoints/mamba_fusion_s.safetensors"
METADATA = ROOT / "models/checkpoints/mamba_fusion_s.metadata.json"


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
    assert _sha256(CHECKPOINT) == metadata["artifact_sha256"]
    assert _sha256(ROOT / metadata["training_script"]) == metadata["training_script_sha256"]
    for artifact in metadata["dataset"]["artifacts"].values():
        assert _sha256(ROOT / artifact["path"]) == artifact["sha256"]
    assert metadata["architecture"]["parameter_count"] == 412688


def test_checkpoint_reproduces_holdout_metrics_and_beats_mean_fusion() -> None:
    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    artifacts = metadata["dataset"]["artifacts"]
    inputs = np.load(ROOT / artifacts["inputs"]["path"], allow_pickle=False)
    masks = np.load(ROOT / artifacts["masks"]["path"], allow_pickle=False)
    targets = np.load(ROOT / artifacts["targets"]["path"], allow_pickle=False)
    model = MultimodalMambaBlock(256)
    model.load_state_dict(load_file(str(CHECKPOINT), device="cpu"), strict=True)
    model.eval()
    actual = evaluate(model, inputs, masks, targets)
    expected = {key: value for key, value in metadata["evaluation"].items() if key != "method"}
    assert actual == expected
    assert actual["trained_fusion"]["mean_cosine_similarity"] > actual[
        "normalized_mean_baseline"
    ]["mean_cosine_similarity"]
    assert actual["trained_fusion"]["rmse"] < actual["normalized_mean_baseline"]["rmse"]


def test_real_predictor_returns_normalized_checkpoint_backed_embedding() -> None:
    request = json.loads(
        (
            ROOT
            / "examples/multimodal_mamba_fusion/1.0.0/golden_cases/case_001_request.json"
        ).read_text(encoding="utf-8")
    )
    assert tia_predictors.tia_model_loaded("multimodal_mamba_fusion") is True
    output = tia_predictors.predict_multimodal_mamba_fusion(request["inputs"], {})
    vector = output["fused_embeddings"]["T-1"]
    assert len(vector) == 256
    assert math.sqrt(sum(value * value for value in vector)) == pytest.approx(1.0, abs=1e-5)
    assert output["modality_count"] == 2
    assert output["model"]["artifact_sha256"] == _sha256(CHECKPOINT)


def test_unequal_vectors_and_explicit_sensor_association_are_supported() -> None:
    output = tia_predictors.predict_multimodal_mamba_fusion(
        {
            "embeddings": {"S-1": [0.2, 0.3], "S-2": [0.7, 0.1, 0.4, 0.8, 0.2]},
            "tracks": [
                {"track_id": "T-LOCAL", "sensor_id": "S-1"},
                {"track_id": "T-GLOBAL"},
            ],
        },
        {},
    )
    local = output["fused_embeddings"]["T-LOCAL"]
    global_vector = output["fused_embeddings"]["T-GLOBAL"]
    assert len(local) == len(global_vector) == 256
    assert local != global_vector
    assert output["input_embedding_dimensions"] == {"S-1": 2, "S-2": 5}


def test_registry_refuses_missing_checkpoint() -> None:
    with pytest.raises(FileNotFoundError, match="trained multimodal fusion checkpoint"):
        get_mamba_fusion(
            {
                "compute_profile": "small",
                "embed_dim": 256,
                "mamba_checkpoint": "definitely_missing_mamba.safetensors",
                "device": "cpu",
            }
        )

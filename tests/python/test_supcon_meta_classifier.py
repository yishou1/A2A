from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import torch
import torch.nn.functional as F
from safetensors.torch import load_file

from agent.inference.models.supcon_meta import SupConMetaNet
from agent.inference.registry import clear_model_cache, get_supcon_meta
from services.a2a_algorithms_common import tia_predictors


ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT = ROOT / "models/checkpoints/supcon_meta_s.safetensors"
METADATA = ROOT / "models/checkpoints/supcon_meta_s.metadata.json"


def _activate_real_small_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIA_USE_MOCK", "0")
    monkeypatch.setenv("TIA_COMPUTE_PROFILE", "small")
    tia_predictors._config.cache_clear()
    tia_predictors._backend.cache_clear()
    clear_model_cache()


def test_checkpoint_and_reference_dataset_hashes_are_verified() -> None:
    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    assert hashlib.sha256(CHECKPOINT.read_bytes()).hexdigest() == metadata[
        "artifact_sha256"
    ]
    embeddings_path = ROOT / metadata["dataset"]["embeddings_path"]
    labels_path = ROOT / metadata["dataset"]["labels_path"]
    assert hashlib.sha256(embeddings_path.read_bytes()).hexdigest() == metadata[
        "dataset"
    ]["embeddings_sha256"]
    assert hashlib.sha256(labels_path.read_bytes()).hexdigest() == metadata[
        "dataset"
    ]["labels_sha256"]
    assert metadata["evaluation"]["accuracy"] == 1.0
    assert metadata["evaluation"]["macro_f1"] == 1.0


def test_checkpoint_reproduces_reference_classification() -> None:
    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    embeddings = torch.from_numpy(
        np.load(ROOT / metadata["dataset"]["embeddings_path"], allow_pickle=False)
    )
    labels = torch.from_numpy(
        np.load(ROOT / metadata["dataset"]["labels_path"], allow_pickle=False)
    )
    model = SupConMetaNet(in_dim=256, proj_dim=128, num_classes=4)
    state = load_file(str(CHECKPOINT), device="cpu")
    model.load_state_dict(state)
    model.eval()
    with torch.inference_mode():
        projected = model.project(embeddings)
        prototypes = F.normalize(model.prototypes, dim=-1)
        predictions = (projected @ prototypes.T).argmax(dim=1)
    assert float((predictions == labels).float().mean()) == 1.0


def test_real_service_path_uses_trained_small_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _activate_real_small_profile(monkeypatch)
    request = json.loads(
        (
            ROOT
            / "examples/supcon_meta_classifier/1.0.0/golden_cases/case_001_request.json"
        ).read_text(encoding="utf-8")
    )
    assert tia_predictors.tia_model_loaded("supcon_meta_classifier") is True
    outputs = tia_predictors.predict_supcon_meta_classifier(request["inputs"], {})
    assert [item["label"] for item in outputs["classifications"]] == [
        "unknown",
        "neutral",
    ]
    assert outputs["model"]["model_family"] == (
        "supervised_contrastive_prototypical_mlp"
    )
    assert outputs["model"]["artifact_sha256"] == hashlib.sha256(
        CHECKPOINT.read_bytes()
    ).hexdigest()


def test_registry_refuses_missing_checkpoint() -> None:
    clear_model_cache()
    with pytest.raises(FileNotFoundError, match="trained SupCon checkpoint"):
        get_supcon_meta(
            {
                "embed_dim": 256,
                "supcon_checkpoint": "definitely_missing_supcon.pt",
                "compute_profile": "test-missing",
                "device": "cpu",
            }
        )

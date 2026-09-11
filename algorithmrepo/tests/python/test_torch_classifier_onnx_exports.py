from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch
import torch.nn.functional as F
from safetensors.torch import load_file

from agent.inference.models.edl_head import EvidentialHead
from agent.inference.models.supcon_meta import SupConMetaNet


ROOT = Path(__file__).resolve().parents[2]
SUPCON_PACKAGE = ROOT / "examples/supcon_meta_classifier_onnx/1.0.0"
EDL_PACKAGE = ROOT / "examples/edl_evidential_verifier_onnx/1.0.0"


def _load_golden(package: Path) -> tuple[dict, dict]:
    golden = package / "golden_cases"
    return (
        json.loads((golden / "case_001_input.json").read_text(encoding="utf-8")),
        json.loads((golden / "case_001_expected.json").read_text(encoding="utf-8")),
    )


def test_torch_onnx_packages_are_complete_and_hash_verified() -> None:
    expected_classes = {
        "supcon_meta_classifier_onnx": ["friendly", "neutral", "hostile", "unknown"],
        "edl_evidential_verifier_onnx": ["rejected", "verified"],
    }
    for package in (SUPCON_PACKAGE, EDL_PACKAGE):
        for relative_path in (
            "algorithm_card.yaml",
            "input.schema.json",
            "output.schema.json",
            "tensor_contract.yaml",
            "preprocess.yaml",
            "postprocess.yaml",
            "model.onnx",
            "model.metadata.json",
            "golden_cases/case_001_input.json",
            "golden_cases/case_001_expected.json",
        ):
            assert (package / relative_path).is_file(), relative_path

        metadata = json.loads((package / "model.metadata.json").read_text(encoding="utf-8"))
        assert metadata["class_order"] == expected_classes[metadata["model_id"]]
        assert hashlib.sha256((package / "model.onnx").read_bytes()).hexdigest() == metadata[
            "artifact_sha256"
        ]
        source = ROOT / metadata["source_model"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == metadata["source_model_sha256"]
        source_metadata = ROOT / metadata["source_metadata"]
        assert hashlib.sha256(source_metadata.read_bytes()).hexdigest() == metadata[
            "source_metadata_sha256"
        ]
        export_script = ROOT / metadata["export_script"]
        normalized_script = export_script.read_text(encoding="utf-8").replace("\r\n", "\n")
        assert hashlib.sha256(normalized_script.encode("utf-8")).hexdigest() == metadata[
            "export_script_sha256"
        ]


def test_supcon_onnx_matches_trained_pytorch_checkpoint() -> None:
    request, expected = _load_golden(SUPCON_PACKAGE)
    features = np.asarray(request["features"], dtype=np.float32)
    state = load_file(
        str(ROOT / "models/checkpoints/supcon_meta_s.safetensors"), device="cpu"
    )
    model = SupConMetaNet(in_dim=256, proj_dim=128, num_classes=4)
    model.load_state_dict(state, strict=True)
    model.eval()
    with torch.inference_mode():
        probabilities = F.softmax(
            model.project(torch.from_numpy(features))
            @ F.normalize(model.prototypes, dim=-1).T
            / 0.07,
            dim=1,
        ).numpy()

    session = ort.InferenceSession(
        str(SUPCON_PACKAGE / "model.onnx"), providers=["CPUExecutionProvider"]
    )
    indices, actual = session.run(None, {"features": features})
    np.testing.assert_allclose(actual, probabilities, rtol=0.0, atol=2e-6)
    np.testing.assert_array_equal(indices, probabilities.argmax(axis=1))
    np.testing.assert_array_equal(indices, expected["predicted_class_indices"])
    np.testing.assert_allclose(actual, expected["class_probabilities"], rtol=0.0, atol=1e-7)


def test_edl_onnx_matches_trained_pytorch_checkpoint() -> None:
    request, expected = _load_golden(EDL_PACKAGE)
    features = np.asarray(request["features"], dtype=np.float32)
    state = load_file(
        str(ROOT / "models/checkpoints/edl_head_s.safetensors"), device="cpu"
    )
    model = EvidentialHead()
    model.load_state_dict(state, strict=True)
    model.eval()
    with torch.inference_mode():
        evidence, alpha, probabilities, epistemic, _ = model(torch.from_numpy(features))
    references = {
        "predicted_class_indices": probabilities.argmax(dim=1).numpy(),
        "evidence": evidence.numpy(),
        "dirichlet_alpha": alpha.numpy(),
        "class_probabilities": probabilities.numpy(),
        "epistemic_uncertainty": epistemic.numpy(),
    }

    session = ort.InferenceSession(
        str(EDL_PACKAGE / "model.onnx"), providers=["CPUExecutionProvider"]
    )
    names = [output.name for output in session.get_outputs()]
    actual = dict(zip(names, session.run(None, {"features": features})))
    for name, reference in references.items():
        if name == "predicted_class_indices":
            np.testing.assert_array_equal(actual[name], reference)
            np.testing.assert_array_equal(actual[name], expected[name])
        else:
            np.testing.assert_allclose(actual[name], reference, rtol=0.0, atol=5e-6)
            np.testing.assert_allclose(actual[name], expected[name], rtol=0.0, atol=1e-7)

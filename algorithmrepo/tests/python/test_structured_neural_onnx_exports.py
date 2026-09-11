from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch
from safetensors.torch import load_file

from agent.inference.models.graph_relation_gnn import DenseGraphRelationGNN
from agent.inference.models.mamba_fusion import MultimodalMambaBlock


ROOT = Path(__file__).resolve().parents[2]
MAMBA_PACKAGE = ROOT / "examples/multimodal_mamba_fusion_onnx/1.0.0"
GRAPH_PACKAGE = ROOT / "examples/graph_relation_reasoner_onnx/1.0.0"


def _load_golden(package: Path) -> tuple[dict, dict]:
    golden = package / "golden_cases"
    return (
        json.loads((golden / "case_001_input.json").read_text(encoding="utf-8")),
        json.loads((golden / "case_001_expected.json").read_text(encoding="utf-8")),
    )


def test_structured_neural_onnx_packages_are_complete_and_hash_verified() -> None:
    for package in (MAMBA_PACKAGE, GRAPH_PACKAGE):
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
        assert hashlib.sha256((package / "model.onnx").read_bytes()).hexdigest() == metadata[
            "artifact_sha256"
        ]
        source = ROOT / metadata["source_model"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == metadata["source_model_sha256"]
        source_metadata = ROOT / metadata["source_metadata"]
        assert hashlib.sha256(source_metadata.read_bytes()).hexdigest() == metadata[
            "source_metadata_sha256"
        ]
        script = ROOT / metadata["export_script"]
        normalized = script.read_text(encoding="utf-8").replace("\r\n", "\n")
        assert hashlib.sha256(normalized.encode("utf-8")).hexdigest() == metadata[
            "export_script_sha256"
        ]


def test_mamba_fusion_onnx_matches_trained_pytorch_checkpoint() -> None:
    request, expected = _load_golden(MAMBA_PACKAGE)
    sequence = np.asarray(request["sequence"], dtype=np.float32)
    mask = np.asarray(request["mask"], dtype=np.float32)
    model = MultimodalMambaBlock(d_model=256, d_state=16, expand=2)
    model.load_state_dict(
        load_file(str(ROOT / "models/checkpoints/mamba_fusion_s.safetensors"), device="cpu"),
        strict=True,
    )
    model.eval()
    with torch.inference_mode():
        reference = model.fused_tensor(
            torch.from_numpy(sequence), torch.from_numpy(mask)
        ).numpy()

    session = ort.InferenceSession(
        str(MAMBA_PACKAGE / "model.onnx"), providers=["CPUExecutionProvider"]
    )
    actual = session.run(
        ["fused_embeddings"], {"sequence": sequence, "mask": mask}
    )[0]
    np.testing.assert_allclose(actual, reference, rtol=0.0, atol=2e-6)
    np.testing.assert_allclose(
        actual,
        np.asarray(expected["fused_embeddings"], dtype=np.float32),
        rtol=0.0,
        atol=1e-7,
    )
    np.testing.assert_allclose(np.linalg.norm(actual, axis=1), 1.0, rtol=0.0, atol=2e-6)


def test_graph_relation_onnx_matches_trained_pytorch_checkpoint() -> None:
    request, expected = _load_golden(GRAPH_PACKAGE)
    nodes = np.asarray(request["node_features"], dtype=np.float32)
    edges = np.asarray(request["edge_features"], dtype=np.float32)
    mask = np.asarray(request["node_mask"], dtype=np.float32)
    model = DenseGraphRelationGNN()
    model.load_state_dict(
        load_file(
            str(ROOT / "models/checkpoints/graph_relation_gnn_s.safetensors"),
            device="cpu",
        ),
        strict=True,
    )
    model.eval()
    with torch.inference_mode():
        reference_logits = model(
            torch.from_numpy(nodes), torch.from_numpy(edges), torch.from_numpy(mask)
        ).numpy()
    reference_probabilities = 1.0 / (1.0 + np.exp(-reference_logits))

    session = ort.InferenceSession(
        str(GRAPH_PACKAGE / "model.onnx"), providers=["CPUExecutionProvider"]
    )
    actual_logits, actual_probabilities = session.run(
        ["relation_logits", "relation_probabilities"],
        {"node_features": nodes, "edge_features": edges, "node_mask": mask},
    )
    np.testing.assert_allclose(actual_logits, reference_logits, rtol=0.0, atol=1.2e-5)
    np.testing.assert_allclose(
        actual_probabilities, reference_probabilities, rtol=0.0, atol=2e-6
    )
    np.testing.assert_allclose(
        actual_logits,
        np.asarray(expected["relation_logits"], dtype=np.float32),
        rtol=0.0,
        atol=1e-7,
    )
    np.testing.assert_allclose(
        actual_probabilities,
        np.asarray(expected["relation_probabilities"], dtype=np.float32),
        rtol=0.0,
        atol=1e-7,
    )
    np.testing.assert_allclose(actual_logits, actual_logits.transpose(0, 2, 1), atol=1e-6)

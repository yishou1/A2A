#!/usr/bin/env python3
"""Export the trained M15/M20 structured neural models to ONNX."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
import torch
import torch.nn as nn
from safetensors.torch import load_file


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.inference.models.graph_relation_gnn import DenseGraphRelationGNN
from agent.inference.models.mamba_fusion import MultimodalMambaBlock


VERSION = "1.0.0"

MAMBA_SOURCE = ROOT / "models/checkpoints/mamba_fusion_s.safetensors"
MAMBA_SOURCE_METADATA = ROOT / "models/checkpoints/mamba_fusion_s.metadata.json"
MAMBA_PACKAGE = ROOT / "examples/multimodal_mamba_fusion_onnx" / VERSION

GRAPH_SOURCE = ROOT / "models/checkpoints/graph_relation_gnn_s.safetensors"
GRAPH_SOURCE_METADATA = ROOT / "models/checkpoints/graph_relation_gnn_s.metadata.json"
GRAPH_PACKAGE = ROOT / "examples/graph_relation_reasoner_onnx" / VERSION


class MambaFusionOnnx(nn.Module):
    def __init__(self, model: MultimodalMambaBlock) -> None:
        super().__init__()
        self.model = model

    def forward(self, sequence: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        return self.model.fused_tensor(sequence, mask)


class GraphRelationOnnx(nn.Module):
    def __init__(self, model: DenseGraphRelationGNN) -> None:
        super().__init__()
        self.model = model

    def forward(
        self,
        node_features: torch.Tensor,
        edge_features: torch.Tensor,
        node_mask: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        logits = self.model(node_features, edge_features, node_mask)
        return logits, torch.sigmoid(logits)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def export_model(
    model: nn.Module,
    inputs: tuple[torch.Tensor, ...],
    path: Path,
    input_names: list[str],
    output_names: list[str],
    dynamic_axes: dict[str, dict[int, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model,
        inputs,
        str(path),
        export_params=True,
        opset_version=17,
        do_constant_folding=True,
        input_names=input_names,
        output_names=output_names,
        dynamic_axes=dynamic_axes,
        dynamo=False,
    )
    exported = onnx.load(path)
    onnx.checker.check_model(exported)


def set_output_dimension(path: Path, output_name: str, axis: int, value: int) -> None:
    exported = onnx.load(path)
    output = next(item for item in exported.graph.output if item.name == output_name)
    output.type.tensor_type.shape.dim[axis].dim_value = value
    onnx.checker.check_model(exported)
    onnx.save(exported, path)


def metadata_base(
    *,
    algorithm_id: str,
    family: str,
    package: Path,
    source: Path,
    source_metadata: Path,
    maximum_difference: float,
    compared_outputs: list[str],
) -> dict:
    script = Path(__file__).resolve()
    artifact = package / "model.onnx"
    return {
        "model_id": algorithm_id,
        "model_version": VERSION,
        "model_family": family,
        "format": "onnx",
        "artifact_path": str(artifact.relative_to(ROOT)).replace("\\", "/"),
        "artifact_sha256": sha256(artifact),
        "export_script": str(script.relative_to(ROOT)).replace("\\", "/"),
        "export_script_sha256": normalized_text_sha256(script),
        "source_model": str(source.relative_to(ROOT)).replace("\\", "/"),
        "source_model_sha256": sha256(source),
        "source_metadata": str(source_metadata.relative_to(ROOT)).replace("\\", "/"),
        "source_metadata_sha256": sha256(source_metadata),
        "precision": "float32",
        "opset": 17,
        "equivalence": {
            "reference": "source PyTorch checkpoint in evaluation mode",
            "compared_outputs": compared_outputs,
            "maximum_absolute_difference": maximum_difference,
        },
        "runtime_versions": {
            "numpy": np.__version__,
            "onnx": onnx.__version__,
            "onnxruntime": ort.__version__,
            "torch": torch.__version__,
        },
    }


def export_mamba() -> dict:
    metadata = json.loads(MAMBA_SOURCE_METADATA.read_text(encoding="utf-8"))
    if sha256(MAMBA_SOURCE) != metadata["artifact_sha256"]:
        raise ValueError("Mamba source checkpoint SHA256 mismatch")
    model = MultimodalMambaBlock(d_model=256, d_state=16, expand=2)
    model.load_state_dict(load_file(str(MAMBA_SOURCE), device="cpu"), strict=True)
    wrapper = MambaFusionOnnx(model.eval()).eval()

    artifacts = metadata["dataset"]["artifacts"]
    sequences = np.load(ROOT / artifacts["inputs"]["path"], allow_pickle=False)[:1].astype(np.float32)
    masks = np.load(ROOT / artifacts["masks"]["path"], allow_pickle=False)[:1].astype(np.float32)
    sequence_tensor = torch.from_numpy(sequences)
    mask_tensor = torch.from_numpy(masks)
    path = MAMBA_PACKAGE / "model.onnx"
    export_model(
        wrapper,
        (sequence_tensor, mask_tensor),
        path,
        ["sequence", "mask"],
        ["fused_embeddings"],
        {
            "sequence": {0: "batch"},
            "mask": {0: "batch"},
            "fused_embeddings": {0: "batch"},
        },
    )
    set_output_dimension(path, "fused_embeddings", 1, 256)
    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    actual = session.run(
        ["fused_embeddings"], {"sequence": sequences, "mask": masks}
    )[0]
    with torch.inference_mode():
        reference = wrapper(sequence_tensor, mask_tensor).cpu().numpy()
    error = float(np.max(np.abs(actual - reference)))
    write_json(
        MAMBA_PACKAGE / "golden_cases/case_001_input.json",
        {"sequence": sequences.tolist(), "mask": masks.tolist()},
    )
    write_json(
        MAMBA_PACKAGE / "golden_cases/case_001_expected.json",
        {"fused_embeddings": actual.tolist()},
    )
    exported_metadata = metadata_base(
        algorithm_id="multimodal_mamba_fusion_onnx",
        family="onnx_mamba_style_state_space_fusion",
        package=MAMBA_PACKAGE,
        source=MAMBA_SOURCE,
        source_metadata=MAMBA_SOURCE_METADATA,
        maximum_difference=error,
        compared_outputs=["fused_embeddings"],
    )
    exported_metadata.update(
        {
            "input_contract": {
                "sequence": ["batch", 4, 256],
                "mask": ["batch", 4],
                "modality_order": metadata["dataset"]["modalities"],
            },
            "parameter_count": metadata["architecture"]["parameter_count"],
            "source_evaluation": metadata["evaluation"],
            "limitations": [
                "Accepts already-encoded embeddings and does not encode raw image, SAR, radar, or text data.",
                "Returns one global fused embedding per batch row; track-specific association remains in the full Python service.",
                metadata["dataset"]["limitation"],
            ],
        }
    )
    write_json(MAMBA_PACKAGE / "model.metadata.json", exported_metadata)
    return {"maximum_absolute_difference": error, "artifact_bytes": path.stat().st_size}


def export_graph() -> dict:
    metadata = json.loads(GRAPH_SOURCE_METADATA.read_text(encoding="utf-8"))
    if sha256(GRAPH_SOURCE) != metadata["artifact_sha256"]:
        raise ValueError("Graph relation source checkpoint SHA256 mismatch")
    architecture = metadata["architecture"]
    model = DenseGraphRelationGNN(
        node_features=int(architecture["node_feature_count"]),
        edge_features=int(architecture["edge_feature_count"]),
        hidden_size=int(architecture["hidden_size"]),
        message_layers=int(architecture["message_passing_layers"]),
    )
    model.load_state_dict(load_file(str(GRAPH_SOURCE), device="cpu"), strict=True)
    wrapper = GraphRelationOnnx(model.eval()).eval()

    artifacts = metadata["dataset"]["artifacts"]
    nodes = np.load(ROOT / artifacts["holdout_nodes"]["path"], allow_pickle=False)[:1].astype(np.float32)
    edges = np.load(ROOT / artifacts["holdout_edges"]["path"], allow_pickle=False)[:1].astype(np.float32)
    masks = np.load(ROOT / artifacts["holdout_masks"]["path"], allow_pickle=False)[:1].astype(np.float32)
    node_tensor = torch.from_numpy(nodes)
    edge_tensor = torch.from_numpy(edges)
    mask_tensor = torch.from_numpy(masks)
    path = GRAPH_PACKAGE / "model.onnx"
    export_model(
        wrapper,
        (node_tensor, edge_tensor, mask_tensor),
        path,
        ["node_features", "edge_features", "node_mask"],
        ["relation_logits", "relation_probabilities"],
        {
            "node_features": {0: "batch"},
            "edge_features": {0: "batch"},
            "node_mask": {0: "batch"},
            "relation_logits": {0: "batch"},
            "relation_probabilities": {0: "batch"},
        },
    )
    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    actual_logits, actual_probabilities = session.run(
        ["relation_logits", "relation_probabilities"],
        {"node_features": nodes, "edge_features": edges, "node_mask": masks},
    )
    with torch.inference_mode():
        reference_logits, reference_probabilities = wrapper(node_tensor, edge_tensor, mask_tensor)
    error = max(
        float(np.max(np.abs(actual_logits - reference_logits.cpu().numpy()))),
        float(np.max(np.abs(actual_probabilities - reference_probabilities.cpu().numpy()))),
    )
    write_json(
        GRAPH_PACKAGE / "golden_cases/case_001_input.json",
        {
            "node_features": nodes.tolist(),
            "edge_features": edges.tolist(),
            "node_mask": masks.tolist(),
        },
    )
    write_json(
        GRAPH_PACKAGE / "golden_cases/case_001_expected.json",
        {
            "relation_logits": actual_logits.tolist(),
            "relation_probabilities": actual_probabilities.tolist(),
        },
    )
    exported_metadata = metadata_base(
        algorithm_id="graph_relation_reasoner_onnx",
        family="onnx_dense_message_passing_graph_neural_network",
        package=GRAPH_PACKAGE,
        source=GRAPH_SOURCE,
        source_metadata=GRAPH_SOURCE_METADATA,
        maximum_difference=error,
        compared_outputs=["relation_logits", "relation_probabilities"],
    )
    exported_metadata.update(
        {
            "input_contract": {
                "node_features": ["batch", 10, 7],
                "edge_features": ["batch", 10, 10, 6],
                "node_mask": ["batch", 10],
            },
            "parameter_count": architecture["parameter_count"],
            "source_evaluation": metadata["evaluation"],
            "limitations": [
                "Accepts normalized graph tensors with a fixed maximum of 10 nodes.",
                "Returns relation matrices; track preprocessing, thresholding, pair labels, and connected-component groups remain caller responsibilities.",
                metadata["dataset"]["limitation"],
            ],
        }
    )
    write_json(GRAPH_PACKAGE / "model.metadata.json", exported_metadata)
    return {"maximum_absolute_difference": error, "artifact_bytes": path.stat().st_size}


def export_all() -> None:
    results = {
        "multimodal_mamba_fusion_onnx": export_mamba(),
        "graph_relation_reasoner_onnx": export_graph(),
    }
    if any(result["maximum_absolute_difference"] > 1e-4 for result in results.values()):
        raise RuntimeError(f"ONNX equivalence tolerance exceeded: {results}")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    export_all()

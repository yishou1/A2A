#!/usr/bin/env python3
"""Export the trained M06/M14 PyTorch classifiers as native ONNX graphs."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
import torch
import torch.nn.functional as F
from onnx import TensorProto, checker, helper, numpy_helper
from safetensors.torch import load_file

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.inference.models.edl_head import EvidentialHead
from agent.inference.models.supcon_meta import SupConMetaNet


VERSION = "1.0.0"

SUPCON_SOURCE = ROOT / "models" / "checkpoints" / "supcon_meta_s.safetensors"
SUPCON_SOURCE_METADATA = SUPCON_SOURCE.with_suffix(".metadata.json")
SUPCON_PACKAGE = ROOT / "examples" / "supcon_meta_classifier_onnx" / VERSION
SUPCON_CLASSES = ["friendly", "neutral", "hostile", "unknown"]
SUPCON_TEMPERATURE = 0.07

EDL_SOURCE = ROOT / "models" / "checkpoints" / "edl_head_s.safetensors"
EDL_SOURCE_METADATA = EDL_SOURCE.with_suffix(".metadata.json")
EDL_PACKAGE = ROOT / "examples" / "edl_evidential_verifier_onnx" / VERSION
EDL_CLASSES = ["rejected", "verified"]
EDL_FEATURES = [
    "detector_confidence",
    "bbox_width_normalized",
    "bbox_height_normalized",
    "bbox_center_x_normalized",
    "bbox_center_y_normalized",
    "damage_score",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tensor(name: str, value: np.ndarray) -> onnx.TensorProto:
    return numpy_helper.from_array(np.asarray(value), name=name)


def save_checked(model: onnx.ModelProto, path: Path) -> None:
    model.ir_version = 8
    checker.check_model(model)
    path.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, path)


def linear_nodes(prefix: str, source: dict[str, torch.Tensor], input_name: str) -> tuple[list, list, str]:
    weight = source[f"{prefix}.weight"].detach().cpu().numpy().astype(np.float32).T
    bias = source[f"{prefix}.bias"].detach().cpu().numpy().astype(np.float32)
    stem = prefix.replace(".", "_")
    matmul_output = f"{stem}_matmul"
    output = f"{stem}_output"
    initializers = [tensor(f"{stem}_weight", weight), tensor(f"{stem}_bias", bias)]
    nodes = [
        helper.make_node("MatMul", [input_name, f"{stem}_weight"], [matmul_output]),
        helper.make_node("Add", [matmul_output, f"{stem}_bias"], [output]),
    ]
    return nodes, initializers, output


def export_supcon(state: dict[str, torch.Tensor], path: Path) -> None:
    nodes1, init1, hidden = linear_nodes("encoder.0", state, "features")
    nodes2, init2, projected = linear_nodes("encoder.2", state, "relu_hidden")
    prototypes = F.normalize(state["prototypes"], dim=-1).cpu().numpy().astype(np.float32).T
    initializers = init1 + init2 + [
        tensor("prototype_columns", prototypes),
        tensor("normalization_axes", np.asarray([1], dtype=np.int64)),
        tensor("normalization_epsilon", np.asarray(1e-12, dtype=np.float32)),
        tensor("temperature", np.asarray(SUPCON_TEMPERATURE, dtype=np.float32)),
    ]
    nodes = nodes1 + [helper.make_node("Relu", [hidden], ["relu_hidden"])] + nodes2 + [
        helper.make_node("Mul", [projected, projected], ["projected_squared"]),
        helper.make_node(
            "ReduceSum",
            ["projected_squared", "normalization_axes"],
            ["projected_sum_squared"],
            keepdims=1,
        ),
        helper.make_node("Sqrt", ["projected_sum_squared"], ["projected_norm"]),
        helper.make_node(
            "Max", ["projected_norm", "normalization_epsilon"], ["safe_projected_norm"]
        ),
        helper.make_node("Div", [projected, "safe_projected_norm"], ["normalized_projection"]),
        helper.make_node(
            "MatMul", ["normalized_projection", "prototype_columns"], ["similarities"]
        ),
        helper.make_node("Div", ["similarities", "temperature"], ["scaled_similarities"]),
        helper.make_node(
            "Softmax", ["scaled_similarities"], ["class_probabilities"], axis=1
        ),
        helper.make_node(
            "ArgMax",
            ["class_probabilities"],
            ["predicted_class_indices"],
            axis=1,
            keepdims=0,
        ),
    ]
    graph = helper.make_graph(
        nodes,
        "supcon_meta_classifier",
        [helper.make_tensor_value_info("features", TensorProto.FLOAT, [None, 256])],
        [
            helper.make_tensor_value_info("predicted_class_indices", TensorProto.INT64, [None]),
            helper.make_tensor_value_info("class_probabilities", TensorProto.FLOAT, [None, 4]),
        ],
        initializers,
    )
    model = helper.make_model(
        graph,
        producer_name="algolib-pytorch-onnx-exporter",
        opset_imports=[helper.make_operatorsetid("", 13)],
    )
    save_checked(model, path)


def export_edl(state: dict[str, torch.Tensor], path: Path) -> None:
    nodes1, init1, hidden = linear_nodes("net.0", state, "features")
    nodes2, init2, logits = linear_nodes("net.2", state, "relu_hidden")
    initializers = init1 + init2 + [
        tensor("one", np.asarray(1.0, dtype=np.float32)),
        tensor("class_count", np.asarray(2.0, dtype=np.float32)),
        tensor("class_axis", np.asarray([1], dtype=np.int64)),
    ]
    nodes = nodes1 + [helper.make_node("Relu", [hidden], ["relu_hidden"])] + nodes2 + [
        helper.make_node("Softplus", [logits], ["evidence"]),
        helper.make_node("Add", ["evidence", "one"], ["dirichlet_alpha"]),
        helper.make_node(
            "ReduceSum", ["dirichlet_alpha", "class_axis"], ["dirichlet_strength"], keepdims=1
        ),
        helper.make_node(
            "Div", ["dirichlet_alpha", "dirichlet_strength"], ["class_probabilities"]
        ),
        helper.make_node(
            "Div", ["class_count", "dirichlet_strength"], ["epistemic_uncertainty_2d"]
        ),
        helper.make_node(
            "Squeeze",
            ["epistemic_uncertainty_2d", "class_axis"],
            ["epistemic_uncertainty"],
        ),
        helper.make_node(
            "ArgMax",
            ["class_probabilities"],
            ["predicted_class_indices"],
            axis=1,
            keepdims=0,
        ),
    ]
    graph = helper.make_graph(
        nodes,
        "edl_evidential_verifier",
        [helper.make_tensor_value_info("features", TensorProto.FLOAT, [None, 6])],
        [
            helper.make_tensor_value_info("predicted_class_indices", TensorProto.INT64, [None]),
            helper.make_tensor_value_info("evidence", TensorProto.FLOAT, [None, 2]),
            helper.make_tensor_value_info("dirichlet_alpha", TensorProto.FLOAT, [None, 2]),
            helper.make_tensor_value_info("class_probabilities", TensorProto.FLOAT, [None, 2]),
            helper.make_tensor_value_info("epistemic_uncertainty", TensorProto.FLOAT, [None]),
        ],
        initializers,
    )
    model = helper.make_model(
        graph,
        producer_name="algolib-pytorch-onnx-exporter",
        opset_imports=[helper.make_operatorsetid("", 13)],
    )
    save_checked(model, path)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def metadata_base(
    *, algorithm_id: str, family: str, package: Path, source: Path,
    source_metadata: Path, feature_order: list[str], class_order: list[str],
    maximum_difference: float, equivalence_outputs: list[str],
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
        "feature_order": feature_order,
        "class_order": class_order,
        "precision": "float32",
        "equivalence": {
            "reference": "source PyTorch checkpoint in evaluation mode",
            "compared_outputs": equivalence_outputs,
            "maximum_absolute_difference": maximum_difference,
        },
        "runtime_versions": {
            "numpy": np.__version__,
            "onnx": onnx.__version__,
            "onnxruntime": ort.__version__,
            "torch": torch.__version__,
        },
    }


def export_all() -> None:
    supcon_metadata = json.loads(SUPCON_SOURCE_METADATA.read_text(encoding="utf-8"))
    if sha256(SUPCON_SOURCE) != supcon_metadata["artifact_sha256"]:
        raise ValueError("SupCon source checkpoint SHA256 mismatch")
    supcon_state = load_file(str(SUPCON_SOURCE), device="cpu")
    supcon_model = SupConMetaNet(in_dim=256, proj_dim=128, num_classes=4)
    supcon_model.load_state_dict(supcon_state, strict=True)
    supcon_model.eval()
    embeddings = np.load(
        ROOT / supcon_metadata["dataset"]["embeddings_path"], allow_pickle=False
    ).astype(np.float32)
    labels = np.load(
        ROOT / supcon_metadata["dataset"]["labels_path"], allow_pickle=False
    )
    selected = [int(np.flatnonzero(labels == class_index)[0]) for class_index in range(4)]
    supcon_features = embeddings[selected]
    supcon_path = SUPCON_PACKAGE / "model.onnx"
    export_supcon(supcon_state, supcon_path)
    supcon_session = ort.InferenceSession(str(supcon_path), providers=["CPUExecutionProvider"])
    supcon_indices, supcon_probabilities = supcon_session.run(None, {"features": supcon_features})
    with torch.inference_mode():
        projection = supcon_model.project(torch.from_numpy(supcon_features))
        prototypes = F.normalize(supcon_model.prototypes, dim=-1)
        supcon_reference = F.softmax(
            projection @ prototypes.T / SUPCON_TEMPERATURE, dim=1
        ).cpu().numpy()
    supcon_error = float(np.max(np.abs(supcon_reference - supcon_probabilities)))
    write_json(SUPCON_PACKAGE / "golden_cases" / "case_001_input.json", {"features": supcon_features.tolist()})
    write_json(
        SUPCON_PACKAGE / "golden_cases" / "case_001_expected.json",
        {
            "predicted_class_indices": np.asarray(supcon_indices, dtype=np.int64).tolist(),
            "class_probabilities": np.asarray(supcon_probabilities, dtype=np.float32).tolist(),
        },
    )
    supcon_export_metadata = metadata_base(
        algorithm_id="supcon_meta_classifier_onnx",
        family="onnx_supervised_contrastive_prototypical_mlp",
        package=SUPCON_PACKAGE,
        source=SUPCON_SOURCE,
        source_metadata=SUPCON_SOURCE_METADATA,
        feature_order=[f"embedding_{index}" for index in range(256)],
        class_order=SUPCON_CLASSES,
        maximum_difference=supcon_error,
        equivalence_outputs=["class_probabilities", "predicted_class_indices"],
    )
    supcon_export_metadata.update(
        {
            "temperature": SUPCON_TEMPERATURE,
            "parameter_count": supcon_metadata["architecture"]["parameter_count"],
            "source_evaluation": supcon_metadata["evaluation"],
            "limitations": [
                "Uses frozen learned prototypes and does not apply per-request support-shot updates.",
                supcon_metadata["dataset"]["limitation"],
            ],
        }
    )
    write_json(SUPCON_PACKAGE / "model.metadata.json", supcon_export_metadata)

    edl_metadata = json.loads(EDL_SOURCE_METADATA.read_text(encoding="utf-8"))
    if sha256(EDL_SOURCE) != edl_metadata["artifact_sha256"]:
        raise ValueError("EDL source checkpoint SHA256 mismatch")
    edl_state = load_file(str(EDL_SOURCE), device="cpu")
    edl_model = EvidentialHead()
    edl_model.load_state_dict(edl_state, strict=True)
    edl_model.eval()
    edl_features = np.asarray(
        [
            [0.91, 70 / 640, 70 / 640, 45 / 640, 55 / 640, 0.0],
            [0.42, 20 / 640, 50 / 640, 130 / 640, 65 / 640, 0.0],
            [0.60, 60 / 640, 80 / 640, 130 / 640, 140 / 640, 0.0],
        ],
        dtype=np.float32,
    )
    edl_path = EDL_PACKAGE / "model.onnx"
    export_edl(edl_state, edl_path)
    edl_session = ort.InferenceSession(str(edl_path), providers=["CPUExecutionProvider"])
    edl_outputs = edl_session.run(None, {"features": edl_features})
    with torch.inference_mode():
        evidence, alpha, probabilities, epistemic, _ = edl_model(torch.from_numpy(edl_features))
    edl_reference = [
        probabilities.argmax(dim=1).cpu().numpy(),
        evidence.cpu().numpy(),
        alpha.cpu().numpy(),
        probabilities.cpu().numpy(),
        epistemic.cpu().numpy(),
    ]
    edl_error = max(
        float(np.max(np.abs(reference.astype(np.float32) - actual.astype(np.float32))))
        for reference, actual in zip(edl_reference[1:], edl_outputs[1:])
    )
    edl_names = [output.name for output in edl_session.get_outputs()]
    write_json(EDL_PACKAGE / "golden_cases" / "case_001_input.json", {"features": edl_features.tolist()})
    write_json(
        EDL_PACKAGE / "golden_cases" / "case_001_expected.json",
        {
            name: np.asarray(value).tolist()
            for name, value in zip(edl_names, edl_outputs)
        },
    )
    edl_export_metadata = metadata_base(
        algorithm_id="edl_evidential_verifier_onnx",
        family="onnx_dirichlet_evidential_neural_classifier",
        package=EDL_PACKAGE,
        source=EDL_SOURCE,
        source_metadata=EDL_SOURCE_METADATA,
        feature_order=EDL_FEATURES,
        class_order=EDL_CLASSES,
        maximum_difference=edl_error,
        equivalence_outputs=edl_names,
    )
    edl_export_metadata.update(
        {
            "parameter_count": edl_metadata["architecture"]["parameter_count"],
            "source_evaluation": edl_metadata["evaluation"],
            "decision_policy": edl_metadata["decision_policy"],
            "limitations": [
                "Returns neural evidence tensors only; policy thresholds and manual-review routing remain caller responsibilities.",
                "Aleatoric uncertainty is available in the full Python package but omitted from this portable graph.",
                edl_metadata["dataset"]["limitation"],
            ],
        }
    )
    write_json(EDL_PACKAGE / "model.metadata.json", edl_export_metadata)

    if supcon_error > 1e-5 or edl_error > 1e-5:
        raise RuntimeError(f"ONNX equivalence tolerance exceeded: supcon={supcon_error}, edl={edl_error}")
    print(json.dumps({
        "supcon_meta_classifier_onnx": {"maximum_absolute_difference": supcon_error},
        "edl_evidential_verifier_onnx": {"maximum_absolute_difference": edl_error},
    }, indent=2))


if __name__ == "__main__":
    export_all()

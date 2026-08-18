#!/usr/bin/env python3
"""Export the frozen M05/M07 sklearn classifiers as native ONNX graphs."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import joblib
import numpy as np
import onnx
import onnxruntime as ort
from onnx import TensorProto, checker, helper


ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.0.0"

RF_SOURCE = ROOT / "models" / "threat_priority_random_forest.joblib"
RF_SOURCE_METADATA = ROOT / "models" / "threat_priority_random_forest.metadata.json"
RF_PACKAGE = ROOT / "examples" / "threat_priority_random_forest_onnx" / VERSION
RF_CLASSES = ["low", "medium", "high"]

GNB_SOURCE = ROOT / "models" / "intent_gaussian_naive_bayes.joblib"
GNB_SOURCE_METADATA = ROOT / "models" / "intent_gaussian_naive_bayes.metadata.json"
GNB_PACKAGE = ROOT / "examples" / "intent_gaussian_naive_bayes_onnx" / VERSION
GNB_CLASSES = ["benign", "surveillance", "hostile"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def save_checked(model: onnx.ModelProto, path: Path) -> None:
    model.ir_version = 8
    checker.check_model(model)
    path.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, path)


def export_random_forest(estimator, path: Path) -> dict:
    source_classes = [str(value) for value in estimator.classes_]
    desired_indices = [source_classes.index(label) for label in RF_CLASSES]
    tree_count = len(estimator.estimators_)

    node_tree_ids: list[int] = []
    node_ids: list[int] = []
    node_feature_ids: list[int] = []
    node_modes: list[str] = []
    node_values: list[float] = []
    node_true_ids: list[int] = []
    node_false_ids: list[int] = []
    node_missing_tracks_true: list[int] = []
    class_tree_ids: list[int] = []
    class_node_ids: list[int] = []
    class_ids: list[int] = []
    class_weights: list[float] = []

    for tree_id, fitted_tree in enumerate(estimator.estimators_):
        tree = fitted_tree.tree_
        for node_id in range(tree.node_count):
            left = int(tree.children_left[node_id])
            right = int(tree.children_right[node_id])
            is_leaf = left == right
            node_tree_ids.append(tree_id)
            node_ids.append(node_id)
            node_feature_ids.append(0 if is_leaf else int(tree.feature[node_id]))
            node_modes.append("LEAF" if is_leaf else "BRANCH_LEQ")
            node_values.append(0.0 if is_leaf else float(tree.threshold[node_id]))
            node_true_ids.append(0 if is_leaf else left)
            node_false_ids.append(0 if is_leaf else right)
            node_missing_tracks_true.append(0)

            if is_leaf:
                counts = np.asarray(tree.value[node_id][0], dtype=np.float64)
                probabilities = counts / max(float(counts.sum()), 1.0)
                for class_id, probability in enumerate(probabilities):
                    class_tree_ids.append(tree_id)
                    class_node_ids.append(node_id)
                    class_ids.append(class_id)
                    class_weights.append(float(probability) / tree_count)

    features = helper.make_tensor_value_info("features", TensorProto.FLOAT, [None, 5])
    predicted_indices = helper.make_tensor_value_info(
        "predicted_class_indices", TensorProto.INT64, [None]
    )
    probabilities = helper.make_tensor_value_info(
        "class_probabilities", TensorProto.FLOAT, [None, len(RF_CLASSES)]
    )
    gather_indices = helper.make_tensor(
        "class_reorder_indices",
        TensorProto.INT64,
        [len(desired_indices)],
        desired_indices,
    )

    nodes = [
        helper.make_node(
            "TreeEnsembleClassifier",
            ["features"],
            ["source_class_index", "source_probabilities"],
            domain="ai.onnx.ml",
            name="random_forest_classifier",
            classlabels_int64s=list(range(len(source_classes))),
            class_ids=class_ids,
            class_nodeids=class_node_ids,
            class_treeids=class_tree_ids,
            class_weights=class_weights,
            nodes_falsenodeids=node_false_ids,
            nodes_featureids=node_feature_ids,
            nodes_missing_value_tracks_true=node_missing_tracks_true,
            nodes_modes=node_modes,
            nodes_nodeids=node_ids,
            nodes_treeids=node_tree_ids,
            nodes_truenodeids=node_true_ids,
            nodes_values=node_values,
            post_transform="NONE",
        ),
        helper.make_node(
            "Gather",
            ["source_probabilities", "class_reorder_indices"],
            ["class_probabilities"],
            axis=1,
            name="semantic_class_reorder",
        ),
        helper.make_node(
            "ArgMax",
            ["class_probabilities"],
            ["predicted_class_indices"],
            axis=1,
            keepdims=0,
            name="predicted_semantic_class",
        ),
    ]
    graph = helper.make_graph(
        nodes,
        "threat_priority_random_forest",
        [features],
        [predicted_indices, probabilities],
        [gather_indices],
    )
    model = helper.make_model(
        graph,
        producer_name="algolib-sklearn-onnx-exporter",
        opset_imports=[
            helper.make_operatorsetid("", 13),
            helper.make_operatorsetid("ai.onnx.ml", 3),
        ],
    )
    save_checked(model, path)
    return {
        "tree_count": tree_count,
        "total_node_count": len(node_ids),
        "source_class_order": source_classes,
        "output_class_order": RF_CLASSES,
    }


def export_gaussian_nb(estimator, path: Path) -> dict:
    source_classes = [str(value) for value in estimator.classes_]
    desired_indices = [source_classes.index(label) for label in GNB_CLASSES]
    theta = np.asarray(estimator.theta_[desired_indices], dtype=np.float32)
    variances = np.asarray(estimator.var_[desired_indices], dtype=np.float32)
    log_normalizers = np.log(np.float32(2.0 * math.pi) * variances).astype(np.float32)
    log_priors = np.log(np.asarray(estimator.class_prior_[desired_indices], dtype=np.float32))

    features = helper.make_tensor_value_info("features", TensorProto.FLOAT, [None, 5])
    predicted_indices = helper.make_tensor_value_info(
        "predicted_class_indices", TensorProto.INT64, [None]
    )
    probabilities = helper.make_tensor_value_info(
        "class_probabilities", TensorProto.FLOAT, [None, len(GNB_CLASSES)]
    )
    initializers = [
        helper.make_tensor("theta", TensorProto.FLOAT, theta.shape, theta.flatten().tolist()),
        helper.make_tensor(
            "variances", TensorProto.FLOAT, variances.shape, variances.flatten().tolist()
        ),
        helper.make_tensor(
            "log_normalizers",
            TensorProto.FLOAT,
            log_normalizers.shape,
            log_normalizers.flatten().tolist(),
        ),
        helper.make_tensor(
            "log_priors", TensorProto.FLOAT, log_priors.shape, log_priors.flatten().tolist()
        ),
        helper.make_tensor("unsqueeze_axes", TensorProto.INT64, [1], [1]),
        helper.make_tensor("reduce_axes", TensorProto.INT64, [1], [2]),
        helper.make_tensor("negative_half", TensorProto.FLOAT, [], [-0.5]),
    ]
    nodes = [
        helper.make_node(
            "Unsqueeze", ["features", "unsqueeze_axes"], ["expanded_features"]
        ),
        helper.make_node("Sub", ["expanded_features", "theta"], ["centered"]),
        helper.make_node("Mul", ["centered", "centered"], ["squared_difference"]),
        helper.make_node(
            "Div", ["squared_difference", "variances"], ["scaled_squared_difference"]
        ),
        helper.make_node(
            "Add",
            ["scaled_squared_difference", "log_normalizers"],
            ["feature_log_terms"],
        ),
        helper.make_node(
            "ReduceSum",
            ["feature_log_terms", "reduce_axes"],
            ["summed_log_terms"],
            keepdims=0,
        ),
        helper.make_node(
            "Mul", ["summed_log_terms", "negative_half"], ["conditional_log_likelihood"]
        ),
        helper.make_node(
            "Add", ["conditional_log_likelihood", "log_priors"], ["joint_log_likelihood"]
        ),
        helper.make_node(
            "Softmax",
            ["joint_log_likelihood"],
            ["class_probabilities"],
            axis=1,
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
        "intent_gaussian_naive_bayes",
        [features],
        [predicted_indices, probabilities],
        initializers,
    )
    model = helper.make_model(
        graph,
        producer_name="algolib-sklearn-onnx-exporter",
        opset_imports=[helper.make_operatorsetid("", 13)],
    )
    save_checked(model, path)
    return {
        "source_class_order": source_classes,
        "output_class_order": GNB_CLASSES,
        "parameter_count": int(theta.size + variances.size + log_priors.size),
    }


def run_onnx(path: Path, features: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    indices, probabilities = session.run(
        ["predicted_class_indices", "class_probabilities"],
        {"features": np.asarray(features, dtype=np.float32)},
    )
    return np.asarray(indices, dtype=np.int64), np.asarray(probabilities, dtype=np.float32)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def metadata_base(
    *,
    algorithm_id: str,
    family: str,
    package: Path,
    source_model: Path,
    source_metadata: Path,
    feature_order: list[str],
    class_order: list[str],
    equivalence_error: float,
) -> dict:
    script_path = Path(__file__).resolve()
    model_path = package / "model.onnx"
    return {
        "model_id": algorithm_id,
        "model_version": VERSION,
        "model_family": family,
        "format": "onnx",
        "artifact_path": str(model_path.relative_to(ROOT)).replace("\\", "/"),
        "artifact_sha256": sha256(model_path),
        "export_script": str(script_path.relative_to(ROOT)).replace("\\", "/"),
        "export_script_sha256": normalized_text_sha256(script_path),
        "source_model": str(source_model.relative_to(ROOT)).replace("\\", "/"),
        "source_model_sha256": sha256(source_model),
        "source_metadata": str(source_metadata.relative_to(ROOT)).replace("\\", "/"),
        "source_metadata_sha256": sha256(source_metadata),
        "feature_order": feature_order,
        "class_order": class_order,
        "precision": "float32",
        "equivalence": {
            "reference": "source sklearn predict_proba",
            "maximum_absolute_probability_difference": equivalence_error,
        },
        "runtime_versions": {
            "joblib": joblib.__version__,
            "numpy": np.__version__,
            "onnx": onnx.__version__,
            "onnxruntime": ort.__version__,
        },
    }


def export_all() -> None:
    rf = joblib.load(RF_SOURCE)
    rf_path = RF_PACKAGE / "model.onnx"
    rf_details = export_random_forest(rf, rf_path)
    rf_features = np.asarray(
        [
            [0.95, 5.0, 420.0, 0.9, 0.95],
            [0.12, 140.0, 20.0, 0.1, 0.7],
            [0.55, 65.0, 180.0, 0.5, 0.8],
        ],
        dtype=np.float32,
    )
    rf_indices, rf_probabilities = run_onnx(rf_path, rf_features)
    source_rf_probabilities = rf.predict_proba(rf_features)
    source_rf_probabilities = source_rf_probabilities[
        :, [[str(value) for value in rf.classes_].index(label) for label in RF_CLASSES]
    ]
    rf_error = float(np.max(np.abs(source_rf_probabilities - rf_probabilities)))
    rf_source_metadata = json.loads(RF_SOURCE_METADATA.read_text(encoding="utf-8"))
    rf_metadata = metadata_base(
        algorithm_id="threat_priority_random_forest_onnx",
        family="onnx_ml_random_forest_classifier",
        package=RF_PACKAGE,
        source_model=RF_SOURCE,
        source_metadata=RF_SOURCE_METADATA,
        feature_order=list(rf_source_metadata["features"]),
        class_order=RF_CLASSES,
        equivalence_error=rf_error,
    )
    rf_metadata.update(rf_details)
    rf_metadata["source_evaluation"] = rf_source_metadata["evaluation"]
    write_json(RF_PACKAGE / "model.metadata.json", rf_metadata)
    write_json(
        RF_PACKAGE / "golden_cases" / "case_001_expected.json",
        {
            "predicted_class_indices": rf_indices.tolist(),
            "class_probabilities": rf_probabilities.tolist(),
        },
    )

    gnb = joblib.load(GNB_SOURCE)
    gnb_path = GNB_PACKAGE / "model.onnx"
    gnb_details = export_gaussian_nb(gnb, gnb_path)
    gnb_features = np.asarray(
        [
            [0.12, 0.22, 0.86, 0.05, 0.15],
            [0.62, 0.56, 0.68, 0.30, 0.52],
            [0.91, 0.80, 0.38, 0.93, 0.88],
        ],
        dtype=np.float32,
    )
    gnb_indices, gnb_probabilities = run_onnx(gnb_path, gnb_features)
    source_gnb_probabilities = gnb.predict_proba(gnb_features)
    source_gnb_probabilities = source_gnb_probabilities[
        :, [[str(value) for value in gnb.classes_].index(label) for label in GNB_CLASSES]
    ]
    gnb_error = float(np.max(np.abs(source_gnb_probabilities - gnb_probabilities)))
    gnb_source_metadata = json.loads(GNB_SOURCE_METADATA.read_text(encoding="utf-8"))
    gnb_metadata = metadata_base(
        algorithm_id="intent_gaussian_naive_bayes_onnx",
        family="onnx_gaussian_naive_bayes_classifier",
        package=GNB_PACKAGE,
        source_model=GNB_SOURCE,
        source_metadata=GNB_SOURCE_METADATA,
        feature_order=list(gnb_source_metadata["features"]),
        class_order=GNB_CLASSES,
        equivalence_error=gnb_error,
    )
    gnb_metadata.update(gnb_details)
    gnb_metadata["source_evaluation"] = gnb_source_metadata["evaluation"]
    write_json(GNB_PACKAGE / "model.metadata.json", gnb_metadata)
    write_json(
        GNB_PACKAGE / "golden_cases" / "case_001_expected.json",
        {
            "predicted_class_indices": gnb_indices.tolist(),
            "class_probabilities": gnb_probabilities.tolist(),
        },
    )

    if rf_error > 1e-5 or gnb_error > 1e-5:
        raise RuntimeError(
            f"ONNX equivalence tolerance exceeded: rf={rf_error}, gnb={gnb_error}"
        )

    print(
        json.dumps(
            {
                "threat_priority_random_forest_onnx": {
                    "max_abs_probability_difference": rf_error,
                    **rf_details,
                },
                "intent_gaussian_naive_bayes_onnx": {
                    "max_abs_probability_difference": gnb_error,
                    **gnb_details,
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    export_all()

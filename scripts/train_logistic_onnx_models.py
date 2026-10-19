#!/usr/bin/env python3
"""Train and export reproducible M04 logistic-regression ONNX packages."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
from onnx import TensorProto, checker, helper
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split


ROOT = Path(__file__).resolve().parents[1]
DATASET_SEED = 20260818
MODEL_SEED = 20260819


@dataclass(frozen=True)
class ModelSpec:
    algorithm_id: str
    feature_names: list[str]
    dataset_name: str


DECISION_SPEC = ModelSpec(
    algorithm_id="decision_plan_recommender_onnx",
    feature_names=[
        "coverage",
        "risk_alignment",
        "resource_efficiency",
        "constraint_fit",
        "authorization",
        "lstm_trend",
        "priority",
        "objective_fit",
    ],
    dataset_name="decision_plan_reference.csv",
)

COMPLIANCE_SPEC = ModelSpec(
    algorithm_id="compliance_risk_scorer_onnx",
    feature_names=[
        "blocking_violation_count",
        "warning_violation_count",
        "authorization_status_score",
        "authorization_out_of_scope",
        "rag_evidence_count",
        "law_of_war_rule_hit",
    ],
    dataset_name="compliance_risk_reference.csv",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def generate_decision_rows(count: int, rng: random.Random) -> list[dict]:
    rows = []
    for index in range(count):
        values = [rng.random() for _ in range(8)]
        values[4] = float(rng.random() >= 0.18)
        score = (
            1.25 * values[0]
            + 1.05 * values[1]
            + 0.7 * values[2]
            + 0.95 * values[3]
            + 0.8 * values[4]
            + 0.65 * values[5]
            + 0.45 * values[6]
            + 0.4 * values[7]
            + 0.5 * values[0] * values[3]
            - 3.25
            + rng.gauss(0.0, 0.32)
        )
        rows.append(
            {
                "sample_id": f"decision-reference-{index + 1:04d}",
                **{name: round(value, 6) for name, value in zip(DECISION_SPEC.feature_names, values)},
                "label": int(score >= 0.0),
            }
        )
    return rows


def generate_compliance_rows(count: int, rng: random.Random) -> list[dict]:
    rows = []
    for index in range(count):
        values = [
            float(rng.randint(0, 2)),
            float(rng.randint(0, 4)),
            round(rng.random(), 6),
            float(rng.random() < 0.22),
            float(rng.randint(0, 5)),
            float(rng.random() < 0.3),
        ]
        score = (
            2.2 * values[0]
            + 0.55 * values[1]
            + 1.15 * values[2]
            + 1.35 * values[3]
            + 0.18 * values[4]
            + 0.85 * values[5]
            - 4.25
            + rng.gauss(0.0, 0.42)
        )
        rows.append(
            {
                "sample_id": f"compliance-reference-{index + 1:04d}",
                **{name: value for name, value in zip(COMPLIANCE_SPEC.feature_names, values)},
                "label": int(score >= 0.0),
            }
        )
    return rows


def write_dataset(path: Path, spec: ModelSpec, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["sample_id", *spec.feature_names, "label"],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def export_onnx(path: Path, coefficients: np.ndarray, intercept: float) -> None:
    feature_count = int(coefficients.size)
    features = helper.make_tensor_value_info("features", TensorProto.FLOAT, [1, feature_count])
    probability = helper.make_tensor_value_info("probability", TensorProto.FLOAT, [1, 1])
    weights = helper.make_tensor(
        "weights",
        TensorProto.FLOAT,
        [feature_count, 1],
        coefficients.astype(np.float32).tolist(),
    )
    bias = helper.make_tensor("bias", TensorProto.FLOAT, [1], [float(intercept)])
    nodes = [
        helper.make_node("MatMul", ["features", "weights"], ["linear"], name="linear_matmul"),
        helper.make_node("Add", ["linear", "bias"], ["logit"], name="linear_bias"),
        helper.make_node("Sigmoid", ["logit"], ["probability"], name="sigmoid_probability"),
    ]
    graph = helper.make_graph(nodes, "trained_logistic_regression", [features], [probability], [weights, bias])
    model = helper.make_model(
        graph,
        producer_name="algolib-logistic-trainer",
        opset_imports=[helper.make_operatorsetid("", 13)],
    )
    model.ir_version = 8
    checker.check_model(model)
    path.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, path)


def train_one(spec: ModelSpec, rows: list[dict], dataset_path: Path) -> dict:
    write_dataset(dataset_path, spec, rows)
    features = np.asarray(
        [[float(row[name]) for name in spec.feature_names] for row in rows],
        dtype=np.float64,
    )
    labels = np.asarray([int(row["label"]) for row in rows], dtype=np.int64)
    train_x, test_x, train_y, test_y = train_test_split(
        features,
        labels,
        test_size=0.25,
        random_state=MODEL_SEED,
        stratify=labels,
    )
    estimator = LogisticRegression(
        C=3.0,
        class_weight="balanced",
        max_iter=2000,
        solver="lbfgs",
        random_state=MODEL_SEED,
    )
    estimator.fit(train_x, train_y)
    probabilities = estimator.predict_proba(test_x)[:, 1]
    predictions = (probabilities >= 0.5).astype(np.int64)

    package = ROOT / "examples" / spec.algorithm_id / "1.0.0"
    model_path = package / "model.onnx"
    metadata_path = package / "model.metadata.json"
    export_onnx(model_path, estimator.coef_[0], float(estimator.intercept_[0]))

    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    ort_probabilities = np.asarray(
        [
            float(
                session.run(
                    ["probability"],
                    {"features": row.reshape(1, -1).astype(np.float32)},
                )[0].reshape(-1)[0]
            )
            for row in test_x
        ],
        dtype=np.float64,
    )
    max_export_difference = float(np.max(np.abs(probabilities - ort_probabilities)))
    script_path = Path(__file__).resolve()
    metadata = {
        "model_id": spec.algorithm_id,
        "model_version": "1.0.0",
        "model_family": "trained_logistic_regression",
        "format": "onnx",
        "artifact_path": str(model_path.relative_to(ROOT)).replace("\\", "/"),
        "artifact_sha256": sha256(model_path),
        "training_script": str(script_path.relative_to(ROOT)).replace("\\", "/"),
        "training_script_sha256": normalized_text_sha256(script_path),
        "dataset": {
            "path": str(dataset_path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(dataset_path),
            "source": "deterministic_repository_reference_policy_generator",
            "seed": DATASET_SEED,
            "row_count": len(rows),
            "class_counts": {
                "negative": int(np.sum(labels == 0)),
                "positive": int(np.sum(labels == 1)),
            },
            "limitation": "Synthetic reference-policy labels only; not an operational decision benchmark.",
        },
        "feature_order": spec.feature_names,
        "coefficients": {
            name: round(float(value), 9)
            for name, value in zip(spec.feature_names, estimator.coef_[0])
        },
        "intercept": round(float(estimator.intercept_[0]), 9),
        "hyperparameters": {
            "C": 3.0,
            "class_weight": "balanced",
            "max_iter": 2000,
            "solver": "lbfgs",
            "random_state": MODEL_SEED,
        },
        "evaluation": {
            "method": "stratified_holdout",
            "test_ratio": 0.25,
            "train_count": len(train_x),
            "test_count": len(test_x),
            "accuracy": round(float(accuracy_score(test_y, predictions)), 6),
            "balanced_accuracy": round(float(balanced_accuracy_score(test_y, predictions)), 6),
            "f1": round(float(f1_score(test_y, predictions)), 6),
            "roc_auc": round(float(roc_auc_score(test_y, probabilities)), 6),
            "confusion_matrix": confusion_matrix(test_y, predictions, labels=[0, 1]).tolist(),
            "onnx_sklearn_max_abs_probability_difference": round(max_export_difference, 12),
        },
        "runtime_versions": {
            "scikit_learn": __import__("sklearn").__version__,
            "onnx": onnx.__version__,
            "onnxruntime": ort.__version__,
        },
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=1200)
    args = parser.parse_args()
    if args.rows < 200:
        raise ValueError("each reference dataset requires at least 200 rows")
    rng = random.Random(DATASET_SEED)
    dataset_root = ROOT / "data" / "logistic_onnx"
    reports = [
        train_one(
            DECISION_SPEC,
            generate_decision_rows(args.rows, rng),
            dataset_root / DECISION_SPEC.dataset_name,
        ),
        train_one(
            COMPLIANCE_SPEC,
            generate_compliance_rows(args.rows, rng),
            dataset_root / COMPLIANCE_SPEC.dataset_name,
        ),
    ]
    print(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

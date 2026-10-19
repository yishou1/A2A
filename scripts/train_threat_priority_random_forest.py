#!/usr/bin/env python3
"""Generate the reference dataset and train the M05 random-forest artifact."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from pathlib import Path

import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from sklearn.model_selection import train_test_split


ROOT = Path(__file__).resolve().parents[1]
FEATURE_NAMES = [
    "threat_score",
    "distance_km",
    "speed_mps",
    "asset_value",
    "intel_confidence",
]
CLASS_NAMES = ["low", "medium", "high"]
DATASET_SEED = 20260816
MODEL_SEED = 20260817


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def reference_label(row: dict[str, float]) -> str:
    """Label generated scenarios using the documented reference risk policy."""
    score = (
        0.42 * row["threat_score"]
        + 0.20 * (1.0 - min(row["distance_km"], 150.0) / 150.0)
        + 0.13 * min(row["speed_mps"], 450.0) / 450.0
        + 0.15 * row["asset_value"]
        + 0.10 * row["intel_confidence"]
    )
    if score < 0.43:
        return "low"
    if score < 0.66:
        return "medium"
    return "high"


def generate_reference_rows(count: int, seed: int) -> list[dict[str, float | str]]:
    if count < 120:
        raise ValueError("reference dataset requires at least 120 rows")
    rng = random.Random(seed)
    rows: list[dict[str, float | str]] = []
    for index in range(count):
        numeric = {
            "threat_score": round(rng.uniform(0.02, 0.99), 6),
            "distance_km": round(rng.uniform(1.0, 150.0), 6),
            "speed_mps": round(rng.uniform(0.0, 450.0), 6),
            "asset_value": round(rng.uniform(0.0, 1.0), 6),
            "intel_confidence": round(rng.uniform(0.5, 1.0), 6),
        }
        rows.append(
            {
                "sample_id": f"reference-{index + 1:04d}",
                **numeric,
                "priority_label": reference_label(numeric),
            }
        )
    return rows


def write_dataset(path: Path, rows: list[dict[str, float | str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["sample_id", *FEATURE_NAMES, "priority_label"],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def train(dataset_path: Path, model_path: Path, metadata_path: Path, rows: int) -> dict:
    records = generate_reference_rows(rows, DATASET_SEED)
    write_dataset(dataset_path, records)

    features = [[float(row[name]) for name in FEATURE_NAMES] for row in records]
    labels = [str(row["priority_label"]) for row in records]
    train_x, test_x, train_y, test_y = train_test_split(
        features,
        labels,
        test_size=0.25,
        random_state=MODEL_SEED,
        stratify=labels,
    )
    estimator = RandomForestClassifier(
        n_estimators=160,
        max_depth=8,
        min_samples_leaf=2,
        class_weight="balanced",
        random_state=MODEL_SEED,
        n_jobs=1,
    )
    estimator.fit(train_x, train_y)
    predictions = estimator.predict(test_x)

    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(estimator, model_path, compress=3)
    label_counts = {name: labels.count(name) for name in CLASS_NAMES}
    matrix = confusion_matrix(test_y, predictions, labels=CLASS_NAMES).tolist()
    metadata = {
        "model_id": "threat_priority_random_forest",
        "model_version": "1.0.0",
        "model_family": "sklearn_random_forest_classifier",
        "artifact_path": str(model_path.relative_to(ROOT)).replace("\\", "/"),
        "artifact_sha256": sha256(model_path),
        "training_script": str(Path(__file__).resolve().relative_to(ROOT)).replace("\\", "/"),
        "training_script_sha256": normalized_text_sha256(Path(__file__).resolve()),
        "dataset": {
            "path": str(dataset_path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(dataset_path),
            "source": "deterministic_repository_reference_generator",
            "seed": DATASET_SEED,
            "row_count": len(records),
            "class_counts": label_counts,
            "limitation": "Reference-policy scenarios only; not an operational battlefield benchmark.",
        },
        "features": FEATURE_NAMES,
        "classes": CLASS_NAMES,
        "hyperparameters": {
            "n_estimators": 160,
            "max_depth": 8,
            "min_samples_leaf": 2,
            "class_weight": "balanced",
            "random_state": MODEL_SEED,
            "n_jobs": 1,
        },
        "evaluation": {
            "method": "stratified_holdout",
            "test_ratio": 0.25,
            "train_count": len(train_x),
            "test_count": len(test_x),
            "accuracy": round(float(accuracy_score(test_y, predictions)), 6),
            "macro_f1": round(float(f1_score(test_y, predictions, average="macro")), 6),
            "confusion_matrix_labels": CLASS_NAMES,
            "confusion_matrix": matrix,
        },
        "feature_importances": {
            name: round(float(value), 8)
            for name, value in zip(FEATURE_NAMES, estimator.feature_importances_)
        },
        "runtime_versions": {
            "scikit_learn": __import__("sklearn").__version__,
            "joblib": joblib.__version__,
        },
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-path",
        type=Path,
        default=ROOT / "data" / "threat_priority" / "reference_training_data.csv",
    )
    parser.add_argument(
        "--model-path",
        type=Path,
        default=ROOT / "models" / "threat_priority_random_forest.joblib",
    )
    parser.add_argument(
        "--metadata-path",
        type=Path,
        default=ROOT / "models" / "threat_priority_random_forest.metadata.json",
    )
    parser.add_argument("--rows", type=int, default=360)
    args = parser.parse_args()
    metadata = train(
        args.dataset_path.resolve(),
        args.model_path.resolve(),
        args.metadata_path.resolve(),
        args.rows,
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

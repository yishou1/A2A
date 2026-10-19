#!/usr/bin/env python3
"""Generate reference observations and train the M07 GaussianNB artifact."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from pathlib import Path

import joblib
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, log_loss
from sklearn.model_selection import train_test_split
from sklearn.naive_bayes import GaussianNB


ROOT = Path(__file__).resolve().parents[1]
FEATURE_NAMES = [
    "radar_activity",
    "communication_intensity",
    "movement_consistency",
    "weapon_signature",
    "proximity_score",
]
CLASS_NAMES = ["benign", "surveillance", "hostile"]
DATASET_SEED = 20260818
MODEL_SEED = 20260819
CLASS_PROFILES = {
    "benign": ([0.20, 0.30, 0.75, 0.15, 0.25], [0.16, 0.18, 0.14, 0.12, 0.17]),
    "surveillance": ([0.63, 0.55, 0.65, 0.34, 0.55], [0.17, 0.18, 0.15, 0.16, 0.18]),
    "hostile": ([0.79, 0.72, 0.45, 0.82, 0.76], [0.15, 0.17, 0.18, 0.14, 0.16]),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _bounded_gaussian(rng: random.Random, mean: float, deviation: float) -> float:
    return round(min(1.0, max(0.0, rng.gauss(mean, deviation))), 6)


def generate_reference_rows(per_class: int, seed: int) -> list[dict[str, float | str]]:
    if per_class < 40:
        raise ValueError("at least 40 observations per class are required")
    rng = random.Random(seed)
    rows: list[dict[str, float | str]] = []
    for class_name in CLASS_NAMES:
        means, deviations = CLASS_PROFILES[class_name]
        for index in range(per_class):
            values = [
                _bounded_gaussian(rng, mean, deviation)
                for mean, deviation in zip(means, deviations)
            ]
            rows.append(
                {
                    "sample_id": f"{class_name}-{index + 1:04d}",
                    **dict(zip(FEATURE_NAMES, values)),
                    "intent_label": class_name,
                }
            )
    rng.shuffle(rows)
    return rows


def write_dataset(path: Path, rows: list[dict[str, float | str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["sample_id", *FEATURE_NAMES, "intent_label"],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def train(
    dataset_path: Path,
    model_path: Path,
    metadata_path: Path,
    per_class: int,
) -> dict:
    records = generate_reference_rows(per_class, DATASET_SEED)
    write_dataset(dataset_path, records)
    features = [[float(row[name]) for name in FEATURE_NAMES] for row in records]
    labels = [str(row["intent_label"]) for row in records]
    train_x, test_x, train_y, test_y = train_test_split(
        features,
        labels,
        test_size=0.25,
        random_state=MODEL_SEED,
        stratify=labels,
    )

    estimator = GaussianNB(var_smoothing=1e-9)
    estimator.fit(train_x, train_y)
    predictions = estimator.predict(test_x)
    probabilities = estimator.predict_proba(test_x)

    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(estimator, model_path, compress=3)
    metadata = {
        "model_id": "intent_gaussian_naive_bayes",
        "model_version": "1.0.0",
        "model_family": "sklearn_gaussian_naive_bayes",
        "artifact_path": str(model_path.relative_to(ROOT)).replace("\\", "/"),
        "artifact_sha256": sha256(model_path),
        "training_script": str(Path(__file__).resolve().relative_to(ROOT)).replace("\\", "/"),
        "training_script_sha256": normalized_text_sha256(Path(__file__).resolve()),
        "dataset": {
            "path": str(dataset_path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(dataset_path),
            "source": "deterministic_class_conditional_reference_generator",
            "seed": DATASET_SEED,
            "row_count": len(records),
            "class_counts": {name: labels.count(name) for name in CLASS_NAMES},
            "limitation": "Synthetic reference distributions only; domain calibration is required before operational use.",
        },
        "features": FEATURE_NAMES,
        "classes": CLASS_NAMES,
        "class_profiles": {
            name: {"means": profile[0], "standard_deviations": profile[1]}
            for name, profile in CLASS_PROFILES.items()
        },
        "hyperparameters": {"var_smoothing": 1e-9, "random_state_for_split": MODEL_SEED},
        "learned_parameters": {
            "class_prior": {
                str(name): round(float(value), 8)
                for name, value in zip(estimator.classes_, estimator.class_prior_)
            },
            "feature_means": {
                str(name): [round(float(value), 8) for value in row]
                for name, row in zip(estimator.classes_, estimator.theta_)
            },
            "feature_variances": {
                str(name): [round(float(value), 8) for value in row]
                for name, row in zip(estimator.classes_, estimator.var_)
            },
        },
        "evaluation": {
            "method": "stratified_holdout",
            "test_ratio": 0.25,
            "train_count": len(train_x),
            "test_count": len(test_x),
            "accuracy": round(float(accuracy_score(test_y, predictions)), 6),
            "macro_f1": round(float(f1_score(test_y, predictions, average="macro")), 6),
            "log_loss": round(float(log_loss(test_y, probabilities, labels=estimator.classes_)), 6),
            "confusion_matrix_labels": CLASS_NAMES,
            "confusion_matrix": confusion_matrix(test_y, predictions, labels=CLASS_NAMES).tolist(),
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
        default=ROOT / "data" / "intent_naive_bayes" / "reference_observations.csv",
    )
    parser.add_argument(
        "--model-path",
        type=Path,
        default=ROOT / "models" / "intent_gaussian_naive_bayes.joblib",
    )
    parser.add_argument(
        "--metadata-path",
        type=Path,
        default=ROOT / "models" / "intent_gaussian_naive_bayes.metadata.json",
    )
    parser.add_argument("--per-class", type=int, default=180)
    args = parser.parse_args()
    metadata = train(
        args.dataset_path.resolve(),
        args.model_path.resolve(),
        args.metadata_path.resolve(),
        args.per_class,
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

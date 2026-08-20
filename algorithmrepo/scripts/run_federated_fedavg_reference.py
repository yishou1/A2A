#!/usr/bin/env python3
"""Run a reproducible multi-round federated logistic-regression experiment."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from a2a_algorithms_common.federated_fedavg import federated_average  # noqa: E402


DATASET_SEED = 20260820
EXPERIMENT_SEED = 20260821
CLIENT_IDS = ["client-alpha", "client-bravo", "client-charlie"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sigmoid(value: float) -> float:
    if value >= 0:
        exp_value = math.exp(-value)
        return 1.0 / (1.0 + exp_value)
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def generate_data(train_per_client: int, test_count: int) -> list[dict]:
    rng = random.Random(DATASET_SEED)
    rows: list[dict] = []
    client_offsets = [-0.8, 0.0, 0.8]
    for client_id, offset in zip(CLIENT_IDS, client_offsets):
        for index in range(train_per_client):
            x1 = rng.gauss(offset, 1.0)
            x2 = rng.gauss(-offset * 0.5, 1.1)
            probability = sigmoid(1.45 * x1 - 1.85 * x2 + 0.20)
            label = 1 if rng.random() < probability else 0
            rows.append(
                {
                    "split": "train",
                    "client_id": client_id,
                    "sample_id": f"{client_id}-{index + 1:04d}",
                    "x1": round(x1, 8),
                    "x2": round(x2, 8),
                    "label": label,
                }
            )
    for index in range(test_count):
        x1 = rng.gauss(0.0, 1.25)
        x2 = rng.gauss(0.0, 1.25)
        probability = sigmoid(1.45 * x1 - 1.85 * x2 + 0.20)
        label = 1 if rng.random() < probability else 0
        rows.append(
            {
                "split": "test",
                "client_id": "global-test",
                "sample_id": f"test-{index + 1:04d}",
                "x1": round(x1, 8),
                "x2": round(x2, 8),
                "label": label,
            }
        )
    return rows


def write_dataset(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["split", "client_id", "sample_id", "x1", "x2", "label"],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def train_local(rows: list[dict], global_weights: dict, epochs: int, rate: float) -> dict:
    coefficients = [float(value) for value in global_weights["coefficients"]]
    intercept = float(global_weights["intercept"])
    for _ in range(epochs):
        for row in rows:
            probability = sigmoid(coefficients[0] * row["x1"] + coefficients[1] * row["x2"] + intercept)
            error = probability - row["label"]
            coefficients[0] -= rate * error * row["x1"]
            coefficients[1] -= rate * error * row["x2"]
            intercept -= rate * error
    return {"coefficients": coefficients, "intercept": intercept}


def evaluate(rows: list[dict], weights: dict) -> dict:
    correct = 0
    loss = 0.0
    for row in rows:
        probability = sigmoid(
            weights["coefficients"][0] * row["x1"]
            + weights["coefficients"][1] * row["x2"]
            + weights["intercept"]
        )
        prediction = 1 if probability >= 0.5 else 0
        correct += prediction == row["label"]
        clipped = min(1.0 - 1e-12, max(1e-12, probability))
        loss -= row["label"] * math.log(clipped) + (1 - row["label"]) * math.log(1 - clipped)
    return {
        "accuracy": round(correct / len(rows), 6),
        "log_loss": round(loss / len(rows), 6),
        "sample_count": len(rows),
    }


def run_experiment(
    dataset_path: Path,
    weights_path: Path,
    metadata_path: Path,
    rounds: int,
) -> dict:
    rows = generate_data(train_per_client=120, test_count=360)
    write_dataset(dataset_path, rows)
    client_rows = {
        client_id: [row for row in rows if row["split"] == "train" and row["client_id"] == client_id]
        for client_id in CLIENT_IDS
    }
    test_rows = [row for row in rows if row["split"] == "test"]
    global_weights = {"coefficients": [0.0, 0.0], "intercept": 0.0}
    round_metrics: list[dict] = []
    for round_number in range(1, rounds + 1):
        updates = []
        for client_id in CLIENT_IDS:
            local_weights = train_local(
                client_rows[client_id], global_weights, epochs=3, rate=0.025
            )
            updates.append(
                {
                    "client_id": client_id,
                    "sample_count": len(client_rows[client_id]),
                    "weights": local_weights,
                }
            )
        aggregation = federated_average(
            {"round_id": f"round-{round_number}", "client_updates": updates},
            {"minimum_clients": 3},
        )
        global_weights = aggregation["aggregated_weights"]
        round_metrics.append({"round": round_number, **evaluate(test_rows, global_weights)})

    weights_path.parent.mkdir(parents=True, exist_ok=True)
    weights_path.write_text(
        json.dumps(global_weights, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    implementation_path = ROOT / "services" / "a2a_algorithms_common" / "federated_fedavg.py"
    metadata = {
        "algorithm_id": "federated_fedavg_aggregator",
        "version": "1.0.0",
        "implementation": str(implementation_path.relative_to(ROOT)).replace("\\", "/"),
        "implementation_sha256": normalized_text_sha256(implementation_path),
        "experiment_script": str(Path(__file__).resolve().relative_to(ROOT)).replace("\\", "/"),
        "experiment_script_sha256": normalized_text_sha256(Path(__file__).resolve()),
        "dataset": {
            "path": str(dataset_path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(dataset_path),
            "source": "deterministic_non_iid_logistic_reference_generator",
            "seed": DATASET_SEED,
            "train_clients": len(CLIENT_IDS),
            "train_samples": sum(len(value) for value in client_rows.values()),
            "test_samples": len(test_rows),
            "limitation": "Synthetic non-IID reference experiment; privacy and production network transport are out of scope.",
        },
        "protocol": {
            "aggregation": "FedAvg weighted by client sample_count",
            "rounds": rounds,
            "clients_per_round": len(CLIENT_IDS),
            "local_epochs": 3,
            "local_learning_rate": 0.025,
            "secure_aggregation": False,
            "differential_privacy": False,
        },
        "round_metrics": round_metrics,
        "final_evaluation": round_metrics[-1],
        "final_global_weights": {
            "path": str(weights_path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(weights_path),
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
        default=ROOT / "data" / "federated_fedavg" / "reference_client_data.csv",
    )
    parser.add_argument(
        "--weights-path",
        type=Path,
        default=ROOT / "models" / "federated_fedavg_reference_weights.json",
    )
    parser.add_argument(
        "--metadata-path",
        type=Path,
        default=ROOT / "models" / "federated_fedavg_reference.metadata.json",
    )
    parser.add_argument("--rounds", type=int, default=8)
    args = parser.parse_args()
    report = run_experiment(
        args.dataset_path.resolve(),
        args.weights_path.resolve(),
        args.metadata_path.resolve(),
        args.rounds,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

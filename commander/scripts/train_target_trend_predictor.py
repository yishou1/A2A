#!/usr/bin/env python3
"""Train and export the M16 fixed-window target-trend predictor deterministically."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "examples" / "target_trend_predictor_onnx" / "1.0.0"
DATA_DIR = ROOT / "data" / "target_trend"
DATASET_SEED = 20260828
HOLDOUT_SEED = 20260829
MODEL_SEED = 20260830
FEATURE_ORDER = [
    "risk_score",
    "probability",
    "inverse_priority",
    "resource_pressure",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_dataset(count: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Generate normalized histories and a four-step-ahead latent risk target."""
    rng = np.random.default_rng(seed)
    time = np.arange(16, dtype=np.float32)
    inputs = np.empty((count, 12, 4), dtype=np.float32)
    targets = np.empty((count, 1), dtype=np.float32)
    for row in range(count):
        base = rng.uniform(0.12, 0.78)
        slope = rng.uniform(-0.045, 0.055)
        curvature = rng.uniform(-0.0018, 0.0018)
        amplitude = rng.uniform(0.0, 0.075)
        phase = rng.uniform(-np.pi, np.pi)
        clean_risk = np.clip(
            base
            + slope * time
            + curvature * np.square(time - 5.5)
            + amplitude * np.sin(0.55 * time + phase),
            0.02,
            0.98,
        )
        observed_risk = np.clip(
            clean_risk[:12] + rng.normal(0.0, 0.018, size=12), 0.0, 1.0
        )
        probability = np.clip(
            0.08 + 0.82 * clean_risk[:12] + rng.normal(0.0, 0.025, size=12),
            0.0,
            1.0,
        )
        inverse_priority = np.clip(
            0.92 - 0.64 * clean_risk[:12] + rng.normal(0.0, 0.03, size=12),
            0.0,
            1.0,
        )
        resource_pressure = np.clip(
            0.16
            + 0.58 * clean_risk[:12]
            + 0.08 * np.cos(0.38 * time[:12] + phase)
            + rng.normal(0.0, 0.025, size=12),
            0.0,
            1.0,
        )
        inputs[row] = np.stack(
            [observed_risk, probability, inverse_priority, resource_pressure], axis=1
        )
        targets[row, 0] = clean_risk[15]
    return inputs, targets


def regression_metrics(predictions: np.ndarray, targets: np.ndarray) -> dict[str, float]:
    residual = predictions.reshape(-1) - targets.reshape(-1)
    denominator = float(np.sum((targets - targets.mean()) ** 2))
    r2 = 1.0 - float(np.sum(residual**2)) / max(denominator, 1e-12)
    return {
        "mae": round(float(np.mean(np.abs(residual))), 6),
        "rmse": round(float(np.sqrt(np.mean(residual**2))), 6),
        "r2": round(r2, 6),
    }


def evaluate_onnx(
    model_path: Path, inputs: np.ndarray, targets: np.ndarray
) -> dict[str, dict[str, float]]:
    import onnxruntime as ort

    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    predictions = np.concatenate(
        [
            session.run(
                ["trend_score"],
                {"sequence": row.reshape(1, 12, 4).astype(np.float32)},
            )[0]
            for row in inputs
        ],
        axis=0,
    )
    persistence = inputs[:, -1, 0:1]
    historical_mean = inputs[:, :, 0].mean(axis=1, keepdims=True)
    return {
        "trained_lstm": regression_metrics(predictions, targets),
        "last_observation_baseline": regression_metrics(persistence, targets),
        "historical_mean_baseline": regression_metrics(historical_mean, targets),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-count", type=int, default=3200)
    parser.add_argument("--holdout-count", type=int, default=800)
    parser.add_argument("--epochs", type=int, default=140)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--hidden-size", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=0.002)
    args = parser.parse_args()

    import onnx
    import onnxruntime as ort
    import sklearn
    import torch
    import torch.nn as nn

    class TargetTrendLSTM(nn.Module):
        def __init__(self, hidden_size: int):
            super().__init__()
            self.lstm = nn.LSTM(input_size=4, hidden_size=hidden_size, batch_first=True)
            self.output = nn.Linear(hidden_size, 1)

        def forward(self, sequence):
            encoded, _ = self.lstm(sequence)
            return torch.sigmoid(self.output(encoded[:, -1, :]))

    random.seed(MODEL_SEED)
    np.random.seed(MODEL_SEED)
    torch.manual_seed(MODEL_SEED)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(1)

    train_inputs, train_targets = generate_dataset(args.train_count, DATASET_SEED)
    holdout_inputs, holdout_targets = generate_dataset(args.holdout_count, HOLDOUT_SEED)
    model = TargetTrendLSTM(args.hidden_size)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.learning_rate, weight_decay=1e-4
    )
    loss_function = nn.MSELoss()
    generator = torch.Generator().manual_seed(MODEL_SEED + 1)
    train_x = torch.from_numpy(train_inputs)
    train_y = torch.from_numpy(train_targets)

    for epoch in range(1, args.epochs + 1):
        permutation = torch.randperm(len(train_x), generator=generator)
        losses = []
        for start in range(0, len(train_x), args.batch_size):
            indices = permutation[start : start + args.batch_size]
            prediction = model(train_x[indices])
            loss = loss_function(prediction, train_y[indices])
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            losses.append(float(loss.item()))
        if epoch == 1 or epoch % 25 == 0 or epoch == args.epochs:
            print(f"epoch={epoch:03d} mse={np.mean(losses):.7f}")

    model.eval()
    model_path = PACKAGE / "model.onnx"
    torch.onnx.export(
        model,
        torch.zeros(1, 12, 4, dtype=torch.float32),
        str(model_path),
        input_names=["sequence"],
        output_names=["trend_score"],
        opset_version=17,
        dynamo=False,
    )
    onnx.checker.check_model(onnx.load(model_path))
    evaluation = evaluate_onnx(model_path, holdout_inputs, holdout_targets)
    trained = evaluation["trained_lstm"]
    persistence = evaluation["last_observation_baseline"]
    if trained["rmse"] >= persistence["rmse"]:
        raise RuntimeError(f"trained LSTM did not beat persistence baseline: {evaluation}")
    if trained["r2"] < 0.85:
        raise RuntimeError(f"trained LSTM holdout R2 is below 0.85: {evaluation}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    datasets = {
        "train_inputs": DATA_DIR / "reference_train_inputs.npy",
        "train_targets": DATA_DIR / "reference_train_targets.npy",
        "holdout_inputs": DATA_DIR / "reference_holdout_inputs.npy",
        "holdout_targets": DATA_DIR / "reference_holdout_targets.npy",
    }
    for name, path in datasets.items():
        np.save(path, locals()[name], allow_pickle=False)

    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    golden_path = PACKAGE / "golden_cases" / "case_001_input.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    golden_prediction = session.run(
        ["trend_score"],
        {"sequence": np.asarray(golden["sequence"], dtype=np.float32)},
    )[0]
    (PACKAGE / "golden_cases" / "case_001_expected.json").write_text(
        json.dumps({"trend_score": golden_prediction.tolist()}, indent=2) + "\n",
        encoding="utf-8",
    )

    script_path = Path(__file__).resolve()
    metadata = {
        "model_id": "target_trend_predictor_onnx",
        "model_version": "1.0.0",
        "model_family": "trained_lstm_time_series_regressor",
        "format": "onnx",
        "artifact_path": model_path.relative_to(ROOT).as_posix(),
        "artifact_sha256": sha256(model_path),
        "training_script": script_path.relative_to(ROOT).as_posix(),
        "training_script_sha256": sha256(script_path),
        "dataset": {
            "source": "deterministic_synthetic_target_risk_trajectory_generator",
            "training_seed": DATASET_SEED,
            "holdout_seed": HOLDOUT_SEED,
            "train_count": args.train_count,
            "holdout_count": args.holdout_count,
            "artifacts": {
                name: {
                    "path": path.relative_to(ROOT).as_posix(),
                    "sha256": sha256(path),
                }
                for name, path in datasets.items()
            },
            "limitation": "Synthetic normalized trajectories only; not an operational forecasting benchmark.",
        },
        "input_name": "sequence",
        "output_name": "trend_score",
        "window_steps": 12,
        "forecast_horizon_steps": 4,
        "step_feature_order": FEATURE_ORDER,
        "architecture": {
            "type": "single_layer_lstm_with_sigmoid_regression_head",
            "input_size": 4,
            "hidden_size": args.hidden_size,
            "parameter_count": sum(parameter.numel() for parameter in model.parameters()),
        },
        "training": {
            "objective": "mean_squared_error",
            "optimizer": "AdamW",
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "learning_rate": args.learning_rate,
            "weight_decay": 0.0001,
            "random_state": MODEL_SEED,
        },
        "evaluation": {
            "method": "independent_deterministic_holdout",
            **evaluation,
        },
        "runtime_versions": {
            "numpy": np.__version__,
            "onnx": onnx.__version__,
            "onnxruntime": ort.__version__,
            "scikit_learn": sklearn.__version__,
            "torch": torch.__version__,
        },
    }
    (PACKAGE / "model.metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evaluation, ensure_ascii=False, indent=2))
    print(f"saved={model_path} sha256={metadata['artifact_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

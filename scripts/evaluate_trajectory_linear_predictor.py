#!/usr/bin/env python3
"""Generate and evaluate the reproducible M03 linear-trajectory reference set."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from a2a_algorithms_common.motion_prediction import predict_single_track  # noqa: E402


DATASET_SEED = 20260816


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def generate_reference_records(count: int, seed: int) -> list[dict]:
    if count < 40:
        raise ValueError("reference evaluation requires at least 40 trajectories")
    rng = random.Random(seed)
    records: list[dict] = []
    for index in range(count):
        point_count = rng.randint(5, 12)
        dt = rng.uniform(0.2, 0.8)
        horizon = rng.uniform(0.5, 3.0)
        x0 = rng.uniform(-500.0, 500.0)
        y0 = rng.uniform(-500.0, 500.0)
        vx = rng.uniform(-25.0, 25.0)
        vy = rng.uniform(-25.0, 25.0)
        # Most samples are constant-velocity tracks. A smaller subset contains
        # mild acceleration to measure the known limitation of linear extrapolation.
        accelerated = index % 4 == 0
        ax = rng.uniform(-0.35, 0.35) if accelerated else 0.0
        ay = rng.uniform(-0.35, 0.35) if accelerated else 0.0
        noise_sigma = rng.uniform(0.02, 0.18)
        history = []
        for point_index in range(point_count):
            t = point_index * dt
            true_x = x0 + vx * t + 0.5 * ax * t * t
            true_y = y0 + vy * t + 0.5 * ay * t * t
            history.append(
                {
                    "t": round(t, 6),
                    "x": round(true_x + rng.gauss(0.0, noise_sigma), 6),
                    "y": round(true_y + rng.gauss(0.0, noise_sigma), 6),
                }
            )
        last_t = (point_count - 1) * dt
        future_t = last_t + horizon
        records.append(
            {
                "case_id": f"trajectory-reference-{index + 1:04d}",
                "motion_type": "mild_acceleration" if accelerated else "constant_velocity",
                "track": {
                    "track_id": f"REF-{index + 1:04d}",
                    "history": history,
                    "weapon_prep_sec": round(horizon * 0.4, 6),
                    "flight_time_sec": round(horizon * 0.6, 6),
                },
                "expected": {
                    "future_t": round(future_t, 6),
                    "x": round(x0 + vx * future_t + 0.5 * ax * future_t * future_t, 6),
                    "y": round(y0 + vy * future_t + 0.5 * ay * future_t * future_t, 6),
                    "vx_at_last_observation": round(vx + ax * last_t, 6),
                    "vy_at_last_observation": round(vy + ay * last_t, 6),
                },
            }
        )
    return records


def evaluate(records: list[dict]) -> dict:
    position_errors: list[float] = []
    absolute_x_errors: list[float] = []
    absolute_y_errors: list[float] = []
    velocity_errors: list[float] = []
    by_motion: dict[str, list[float]] = {}
    for record in records:
        result = predict_single_track(record["track"])
        if not result.get("ok"):
            raise ValueError(f"prediction failed for {record['case_id']}: {result}")
        expected = record["expected"]
        x_error = abs(float(result["aim_point"]["x"]) - float(expected["x"]))
        y_error = abs(float(result["aim_point"]["y"]) - float(expected["y"]))
        position_error = math.hypot(x_error, y_error)
        velocity_error = math.hypot(
            float(result["velocity"]["vx"]) - float(expected["vx_at_last_observation"]),
            float(result["velocity"]["vy"]) - float(expected["vy_at_last_observation"]),
        )
        absolute_x_errors.append(x_error)
        absolute_y_errors.append(y_error)
        position_errors.append(position_error)
        velocity_errors.append(velocity_error)
        by_motion.setdefault(record["motion_type"], []).append(position_error)

    ordered = sorted(position_errors)
    p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "method": "deterministic_synthetic_trajectory_holdout",
        "test_count": len(records),
        "position_mae": round(statistics.fmean(position_errors), 6),
        "position_rmse": round(math.sqrt(statistics.fmean(error * error for error in position_errors)), 6),
        "position_p95": round(ordered[p95_index], 6),
        "x_mae": round(statistics.fmean(absolute_x_errors), 6),
        "y_mae": round(statistics.fmean(absolute_y_errors), 6),
        "velocity_vector_mae": round(statistics.fmean(velocity_errors), 6),
        "position_mae_by_motion_type": {
            name: round(statistics.fmean(errors), 6)
            for name, errors in sorted(by_motion.items())
        },
    }


def build(dataset_path: Path, metadata_path: Path, count: int) -> dict:
    records = generate_reference_records(count, DATASET_SEED)
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    evaluation = evaluate(records)
    implementation_path = ROOT / "services" / "a2a_algorithms_common" / "motion_prediction.py"
    script_path = Path(__file__).resolve()
    metadata = {
        "model_id": "trajectory_linear_predictor",
        "model_version": "1.0.0",
        "model_family": "per_request_ordinary_least_squares_2d",
        "training_required": False,
        "fitted_parameters_per_request": ["x_slope", "x_intercept", "y_slope", "y_intercept"],
        "implementation_path": str(implementation_path.relative_to(ROOT)).replace("\\", "/"),
        "implementation_sha256": normalized_text_sha256(implementation_path),
        "evaluation_script": str(script_path.relative_to(ROOT)).replace("\\", "/"),
        "evaluation_script_sha256": normalized_text_sha256(script_path),
        "evaluation_dataset": {
            "path": str(dataset_path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(dataset_path),
            "source": "deterministic_synthetic_motion_generator",
            "seed": DATASET_SEED,
            "row_count": len(records),
            "constant_velocity_count": sum(record["motion_type"] == "constant_velocity" for record in records),
            "mild_acceleration_count": sum(record["motion_type"] == "mild_acceleration" for record in records),
            "limitation": "Synthetic kinematics only; not an operational sensor-track benchmark.",
        },
        "evaluation": evaluation,
        "runtime": {"language": "python", "external_ml_dependencies": []},
    }
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-path",
        type=Path,
        default=ROOT / "data" / "trajectory_linear" / "reference_evaluation_data.json",
    )
    parser.add_argument(
        "--metadata-path",
        type=Path,
        default=ROOT / "models" / "trajectory_linear_predictor.metadata.json",
    )
    parser.add_argument("--count", type=int, default=160)
    args = parser.parse_args()
    metadata = build(args.dataset_path.resolve(), args.metadata_path.resolve(), args.count)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

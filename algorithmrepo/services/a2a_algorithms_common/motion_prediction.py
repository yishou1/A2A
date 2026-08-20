"""Linear regression motion prediction for execution control."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple


DEFAULT_METADATA_RELATIVE_PATH = Path("models/trajectory_linear_predictor.metadata.json")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_track_fixture(path: Path | None = None) -> dict:
    fixture_path = path or (_repo_root() / "data" / "execution_control" / "fixtures" / "track_histories.json")
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def fit_linear(ts: Sequence[float], values: Sequence[float]) -> Tuple[float, float]:
    if len(ts) != len(values):
        raise ValueError("timestamps and values must have the same length")
    if len(ts) < 2:
        value = float(values[0]) if values else 0.0
        return 0.0, value
    mean_t = sum(ts) / len(ts)
    mean_v = sum(values) / len(values)
    numerator = sum((t - mean_t) * (v - mean_v) for t, v in zip(ts, values))
    denominator = sum((t - mean_t) ** 2 for t in ts)
    if denominator <= 0.0:
        raise ValueError("track history must contain at least two distinct timestamps")
    slope = numerator / denominator
    intercept = mean_v - slope * mean_t
    return slope, intercept


def predict_linear(ts: Sequence[float], values: Sequence[float], future_t: float) -> float:
    slope, intercept = fit_linear(ts, values)
    return slope * future_t + intercept


def _r_squared(ts: Sequence[float], values: Sequence[float], slope: float, intercept: float) -> float:
    mean_value = sum(values) / len(values)
    residual = sum((value - (slope * t + intercept)) ** 2 for t, value in zip(ts, values))
    total = sum((value - mean_value) ** 2 for value in values)
    return 1.0 if total <= 1e-15 and residual <= 1e-15 else max(0.0, 1.0 - residual / max(total, 1e-15))


def validate_motion_predictor_artifact() -> dict:
    """Validate source and reference-evaluation identities recorded in metadata."""
    root = _repo_root()
    metadata_path = root / DEFAULT_METADATA_RELATIVE_PATH
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    implementation_path = root / str(metadata["implementation_path"])
    evaluation_path = root / str(metadata["evaluation_dataset"]["path"])
    actual_implementation_hash = _normalized_text_sha256(implementation_path)
    actual_evaluation_hash = _sha256(evaluation_path)
    if actual_implementation_hash != metadata.get("implementation_sha256"):
        raise ValueError("trajectory predictor implementation SHA256 mismatch")
    if actual_evaluation_hash != metadata.get("evaluation_dataset", {}).get("sha256"):
        raise ValueError("trajectory predictor evaluation dataset SHA256 mismatch")
    return metadata


def motion_predictor_loaded() -> bool:
    try:
        validate_motion_predictor_artifact()
        return True
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def motion_predictor_identity() -> dict:
    metadata = validate_motion_predictor_artifact()
    return {
        "model_id": metadata["model_id"],
        "model_version": metadata["model_version"],
        "model_family": metadata["model_family"],
        "implementation_sha256": metadata["implementation_sha256"],
    }


def build_track_histories(results: dict, fixture: dict | None = None) -> List[dict]:
    fusion = (results.get("data_fusion") or {}).get("output_data") or {}
    history = fusion.get("track_history") or fusion.get("tracks") or []
    tracks: List[dict] = []
    if isinstance(history, list) and history:
        for item in history:
            if not isinstance(item, dict):
                continue
            track_id = str(item.get("track_id") or item.get("id") or "")
            points = item.get("history") or item.get("points") or []
            if track_id and isinstance(points, list) and points:
                weapon_prep_value = item.get("weapon_prep_sec")
                flight_time_value = item.get("flight_time_sec")
                tracks.append(
                    {
                        "track_id": track_id,
                        "history": points,
                        "weapon_prep_sec": 2.0 if weapon_prep_value is None else float(weapon_prep_value),
                        "flight_time_sec": 4.0 if flight_time_value is None else float(flight_time_value),
                    }
                )
    if tracks:
        return tracks
    if fixture is not None:
        return list(fixture.get("default_tracks") or [])
    return []


def predict_single_track(track_dict: dict) -> dict:
    """Predict motion for a single track. Returns error if history has fewer than 2 points."""
    history = track_dict.get("history") or []
    points = [point for point in history if isinstance(point, dict)]
    if len(points) < 2:
        return {
            "ok": False,
            "error": {
                "code": "INSUFFICIENT_HISTORY",
                "message": "track history requires at least 2 points",
            },
        }

    samples = [
        (
            float(point["t"]) if point.get("t") is not None else index * 0.1,
            float(point["x"]) if point.get("x") is not None else 0.0,
            float(point["y"]) if point.get("y") is not None else 0.0,
        )
        for index, point in enumerate(points)
    ]
    if not all(math.isfinite(value) for sample in samples for value in sample):
        raise ValueError("track history values must be finite")
    samples.sort(key=lambda sample: sample[0])
    ts = [sample[0] for sample in samples]
    xs = [sample[1] for sample in samples]
    ys = [sample[2] for sample in samples]
    last_t = ts[-1]
    weapon_prep_value = track_dict.get("weapon_prep_sec")
    flight_time_value = track_dict.get("flight_time_sec")
    weapon_prep = 2.0 if weapon_prep_value is None else float(weapon_prep_value)
    flight_time = 4.0 if flight_time_value is None else float(flight_time_value)
    if weapon_prep < 0.0 or flight_time < 0.0:
        raise ValueError("prediction horizon components must be non-negative")
    execute_at = round(last_t + weapon_prep, 3)
    future_t = last_t + weapon_prep + flight_time
    vx, x_intercept = fit_linear(ts, xs)
    vy, y_intercept = fit_linear(ts, ys)
    predicted_x = vx * future_t + x_intercept
    predicted_y = vy * future_t + y_intercept

    return {
        "ok": True,
        "track_id": track_dict.get("track_id"),
        "velocity": {"vx": round(vx, 4), "vy": round(vy, 4)},
        "aim_point": {"x": round(predicted_x, 4), "y": round(predicted_y, 4)},
        "execute_at": execute_at,
        "future_t": round(future_t, 4),
        "model": "linear_regression",
        "fit": {
            "x": {
                "slope": round(vx, 8),
                "intercept": round(x_intercept, 8),
                "r_squared": round(_r_squared(ts, xs, vx, x_intercept), 8),
            },
            "y": {
                "slope": round(vy, 8),
                "intercept": round(y_intercept, 8),
                "r_squared": round(_r_squared(ts, ys, vy, y_intercept), 8),
            },
        },
        "history_points": len(points),
    }


def predict_tracks(tracks: Sequence[dict]) -> Tuple[List[dict], List[dict]]:
    updated_tracks: List[dict] = []
    prediction_details: List[dict] = []
    for track in tracks:
        result = predict_single_track(track)
        if not result.get("ok"):
            continue
        history = track.get("history") or []
        points = [point for point in history if isinstance(point, dict)]
        ts = [float(point.get("t") or index * 0.1) for index, point in enumerate(points)]
        xs = [float(point.get("x") or 0.0) for point in points]
        ys = [float(point.get("y") or 0.0) for point in points]
        last_t = ts[-1]
        current_x = xs[-1]
        current_y = ys[-1]
        updated_tracks.append(
            {
                "track_id": track.get("track_id"),
                "current_point": {"x": round(current_x, 4), "y": round(current_y, 4), "t": round(last_t, 4)},
                "velocity": result["velocity"],
                "history_points": result["history_points"],
            }
        )
        prediction_details.append(
            {
                "track_id": result.get("track_id"),
                "future_t": result.get("future_t"),
                "execute_at": result.get("execute_at"),
                "aim_point": result.get("aim_point"),
                "model": result.get("model"),
                "history_points": result.get("history_points"),
            }
        )
    return updated_tracks, prediction_details

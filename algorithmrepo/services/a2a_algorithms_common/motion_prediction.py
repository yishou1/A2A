"""Linear regression motion prediction for execution control."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np
from filterpy.common import Q_discrete_white_noise
from filterpy.kalman import KalmanFilter
from sklearn.linear_model import LinearRegression

from .algorithm_profiles import normalize_algorithm_profile, profile_config


DEFAULT_METADATA_RELATIVE_PATH = Path("models/trajectory_linear_predictor.metadata.json")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
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
    if len(set(float(value) for value in ts)) < 2:
        raise ValueError("track history must contain at least two distinct timestamps")
    model = LinearRegression().fit(np.asarray(ts, dtype=float).reshape(-1, 1), np.asarray(values, dtype=float))
    return float(model.coef_[0]), float(model.intercept_)


def _kalman_state(
    ts: Sequence[float],
    xs: Sequence[float],
    ys: Sequence[float],
    *,
    process_noise: float,
    measurement_noise: float,
) -> tuple[KalmanFilter, float, float]:
    vx, _ = fit_linear(ts, xs)
    vy, _ = fit_linear(ts, ys)
    state = KalmanFilter(dim_x=4, dim_z=2)
    state.x = np.asarray([xs[0], vx, ys[0], vy], dtype=float)
    state.H = np.asarray([[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]])
    state.P = np.diag([1.0, 10.0, 1.0, 10.0])
    state.R = np.eye(2) * float(measurement_noise)
    previous_t = float(ts[0])
    for current_t, x_value, y_value in zip(ts[1:], xs[1:], ys[1:]):
        dt = float(current_t) - previous_t
        if dt <= 0.0:
            raise ValueError("track timestamps must be strictly increasing")
        state.F = np.asarray(
            [[1.0, dt, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, dt], [0.0, 0.0, 0.0, 1.0]]
        )
        state.Q = Q_discrete_white_noise(dim=2, dt=dt, var=float(process_noise), block_size=2)
        state.predict()
        state.update(np.asarray([x_value, y_value], dtype=float))
        previous_t = float(current_t)
    return state, float(state.x[1]), float(state.x[3])


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
    actual_evaluation_hash = _normalized_text_sha256(evaluation_path)
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
                track = {"track_id": track_id, "history": points}
                if item.get("weapon_prep_sec") is not None:
                    track["weapon_prep_sec"] = float(item.get("weapon_prep_sec"))
                if item.get("flight_time_sec") is not None:
                    track["flight_time_sec"] = float(item.get("flight_time_sec"))
                tracks.append(track)
    if tracks:
        return tracks
    if fixture is not None:
        return list(fixture.get("default_tracks") or [])
    return []


def predict_single_track(track_dict: dict, *, profile: str = "medium") -> dict:
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

    if track_dict.get("weapon_prep_sec") is None or track_dict.get("flight_time_sec") is None:
        return {
            "ok": False,
            "error": {
                "code": "MISSING_PREDICTION_HORIZON",
                "message": "track history requires weapon_prep_sec and flight_time_sec",
            },
        }
    samples = [
        (float(point["t"]), float(point["x"]), float(point["y"]))
        for point in points
        if point.get("t") is not None and point.get("x") is not None and point.get("y") is not None
    ]
    if len(samples) < 2:
        return {
            "ok": False,
            "error": {
                "code": "INCOMPLETE_HISTORY_POINTS",
                "message": "track history points require t, x, and y",
            },
        }
    if not all(math.isfinite(value) for sample in samples for value in sample):
        raise ValueError("track history values must be finite")
    samples.sort(key=lambda sample: sample[0])
    ts = [sample[0] for sample in samples]
    xs = [sample[1] for sample in samples]
    ys = [sample[2] for sample in samples]
    last_t = ts[-1]
    weapon_prep = float(track_dict.get("weapon_prep_sec"))
    flight_time = float(track_dict.get("flight_time_sec"))
    if weapon_prep < 0.0 or flight_time < 0.0:
        raise ValueError("prediction horizon components must be non-negative")
    execute_at = round(last_t + weapon_prep, 3)
    future_t = last_t + weapon_prep + flight_time
    profile = normalize_algorithm_profile(profile)
    config = profile_config("motion_prediction", profile)
    linear_vx, x_intercept = fit_linear(ts, xs)
    linear_vy, y_intercept = fit_linear(ts, ys)
    if config["backend"] == "filterpy_kalman":
        state, vx, vy = _kalman_state(
            ts,
            xs,
            ys,
            process_noise=float(config["process_noise"]),
            measurement_noise=float(config["measurement_noise"]),
        )
        horizon = weapon_prep + flight_time
        state.F = np.asarray(
            [[1.0, horizon, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, horizon], [0.0, 0.0, 0.0, 1.0]]
        )
        state.predict()
        predicted_x, predicted_y = float(state.x[0]), float(state.x[2])
        model_name = "filterpy_constant_velocity_kalman"
    else:
        vx, vy = linear_vx, linear_vy
        predicted_x = vx * future_t + x_intercept
        predicted_y = vy * future_t + y_intercept
        model_name = "sklearn_linear_regression"

    return {
        "ok": True,
        "track_id": track_dict.get("track_id"),
        "velocity": {"vx": round(vx, 4), "vy": round(vy, 4)},
        "aim_point": {"x": round(predicted_x, 4), "y": round(predicted_y, 4)},
        "execute_at": execute_at,
        "future_t": round(future_t, 4),
        "model": model_name,
        "algorithm_profile": profile,
        "fit": {
            "x": {
                "slope": round(linear_vx, 8),
                "intercept": round(x_intercept, 8),
                "r_squared": round(_r_squared(ts, xs, linear_vx, x_intercept), 8),
            },
            "y": {
                "slope": round(linear_vy, 8),
                "intercept": round(y_intercept, 8),
                "r_squared": round(_r_squared(ts, ys, linear_vy, y_intercept), 8),
            },
        },
        "history_points": len(points),
    }


def predict_tracks(tracks: Sequence[dict], *, profile: str = "medium") -> Tuple[List[dict], List[dict]]:
    updated_tracks: List[dict] = []
    prediction_details: List[dict] = []
    for track in tracks:
        result = predict_single_track(track, profile=profile)
        if not result.get("ok"):
            continue
        history = track.get("history") or []
        points = [point for point in history if isinstance(point, dict)]
        usable_points = [
            point
            for point in points
            if point.get("t") is not None and point.get("x") is not None and point.get("y") is not None
        ]
        if len(usable_points) < 2:
            continue
        ts = [float(point.get("t")) for point in usable_points]
        xs = [float(point.get("x")) for point in usable_points]
        ys = [float(point.get("y")) for point in usable_points]
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

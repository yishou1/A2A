"""Linear regression motion prediction for execution control."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_track_fixture(path: Path | None = None) -> dict:
    fixture_path = path or (_repo_root() / "data" / "execution_control" / "fixtures" / "track_histories.json")
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def fit_linear(ts: Sequence[float], values: Sequence[float]) -> Tuple[float, float]:
    if len(ts) < 2:
        value = float(values[0]) if values else 0.0
        return 0.0, value
    mean_t = sum(ts) / len(ts)
    mean_v = sum(values) / len(values)
    numerator = sum((t - mean_t) * (v - mean_v) for t, v in zip(ts, values))
    denominator = sum((t - mean_t) ** 2 for t in ts) or 1.0
    slope = numerator / denominator
    intercept = mean_v - slope * mean_t
    return slope, intercept


def predict_linear(ts: Sequence[float], values: Sequence[float], future_t: float) -> float:
    slope, intercept = fit_linear(ts, values)
    return slope * future_t + intercept


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
                tracks.append(
                    {
                        "track_id": track_id,
                        "history": points,
                        "weapon_prep_sec": float(item.get("weapon_prep_sec") or 2.0),
                        "flight_time_sec": float(item.get("flight_time_sec") or 4.0),
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

    ts = [float(point.get("t") or index * 0.1) for index, point in enumerate(points)]
    xs = [float(point.get("x") or 0.0) for point in points]
    ys = [float(point.get("y") or 0.0) for point in points]
    last_t = ts[-1]
    weapon_prep = float(track_dict.get("weapon_prep_sec") or 2.0)
    flight_time = float(track_dict.get("flight_time_sec") or 4.0)
    execute_at = round(last_t + weapon_prep, 3)
    future_t = last_t + weapon_prep + flight_time
    predicted_x = predict_linear(ts, xs, future_t)
    predicted_y = predict_linear(ts, ys, future_t)
    vx, _ = fit_linear(ts, xs)
    vy, _ = fit_linear(ts, ys)

    return {
        "ok": True,
        "track_id": track_dict.get("track_id"),
        "velocity": {"vx": round(vx, 4), "vy": round(vy, 4)},
        "aim_point": {"x": round(predicted_x, 4), "y": round(predicted_y, 4)},
        "execute_at": execute_at,
        "future_t": round(future_t, 4),
        "model": "linear_regression",
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

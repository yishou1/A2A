"""Linear regression motion prediction for execution control."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_track_fixture(path: Path | None = None) -> dict:
    fixture_path = path or (_project_root() / "data" / "execution_control" / "fixtures" / "track_histories.json")
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
                track = {"track_id": track_id, "history": points}
                if item.get("weapon_prep_sec") is not None:
                    track["weapon_prep_sec"] = float(item.get("weapon_prep_sec"))
                if item.get("flight_time_sec") is not None:
                    track["flight_time_sec"] = float(item.get("flight_time_sec"))
                tracks.append(track)
    if tracks:
        return tracks
    return list((fixture or {}).get("default_tracks") or []) if fixture else []


def predict_tracks(tracks: Sequence[dict]) -> Tuple[List[dict], List[dict]]:
    updated_tracks: List[dict] = []
    prediction_details: List[dict] = []
    for track in tracks:
        history = track.get("history") or []
        points = [point for point in history if isinstance(point, dict)]
        if len(points) < 2:
            continue
        if track.get("weapon_prep_sec") is None or track.get("flight_time_sec") is None:
            continue
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
        weapon_prep = float(track.get("weapon_prep_sec"))
        flight_time = float(track.get("flight_time_sec"))
        execute_at = round(last_t + weapon_prep, 3)
        future_t = last_t + weapon_prep + flight_time
        predicted_x = predict_linear(ts, xs, future_t)
        predicted_y = predict_linear(ts, ys, future_t)
        current_x = xs[-1]
        current_y = ys[-1]
        vx, _ = fit_linear(ts, xs)
        vy, _ = fit_linear(ts, ys)
        updated = {
            "track_id": track.get("track_id"),
            "current_point": {"x": round(current_x, 4), "y": round(current_y, 4), "t": round(last_t, 4)},
            "velocity": {"vx": round(vx, 4), "vy": round(vy, 4)},
            "history_points": len(points),
        }
        updated_tracks.append(updated)
        prediction_details.append(
            {
                "track_id": track.get("track_id"),
                "future_t": round(future_t, 4),
                "execute_at": execute_at,
                "aim_point": {"x": round(predicted_x, 4), "y": round(predicted_y, 4)},
                "model": "linear_regression",
                "history_points": len(usable_points),
            }
        )
    return updated_tracks, prediction_details

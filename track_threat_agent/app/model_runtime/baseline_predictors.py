"""Baseline trajectory predictors for real-data evaluation."""

from __future__ import annotations

from typing import Any, Dict, List

from ..utils import meters_to_lat_lon_delta


def last_position_prediction(sample: Dict[str, Any]) -> List[Dict[str, float]]:
    anchor = sample["history"][-1]
    return [
        {
            "dt_s": float(point["dt_s"]),
            "lat": float(anchor["lat"]),
            "lon": float(anchor["lon"]),
            "alt": float(anchor.get("alt", 0.0)),
        }
        for point in sample.get("future", [])
    ]


def constant_velocity_prediction(sample: Dict[str, Any]) -> List[Dict[str, float]]:
    history = sample.get("history", [])
    if len(history) < 2:
        return last_position_prediction(sample)
    prev = history[-2]
    anchor = history[-1]
    dt = max(1.0, float(anchor["timestamp"]) - float(prev["timestamp"]))
    east_m, north_m = latlon_to_local_m(float(anchor["lat"]), float(anchor["lon"]), float(prev["lat"]), float(prev["lon"]))
    vx = east_m / dt
    vy = north_m / dt
    predictions = []
    for point in sample.get("future", []):
        horizon = float(point["dt_s"])
        d_lat, d_lon = meters_to_lat_lon_delta(vy * horizon, vx * horizon, float(anchor["lat"]))
        predictions.append(
            {
                "dt_s": horizon,
                "lat": float(anchor["lat"]) + d_lat,
                "lon": float(anchor["lon"]) + d_lon,
                "alt": float(anchor.get("alt", 0.0)),
            }
        )
    return predictions


def latlon_to_local_m(lat: float, lon: float, reference_lat: float, reference_lon: float) -> tuple[float, float]:
    cos_lat = max(0.01, __import__("math").cos(__import__("math").radians(reference_lat)))
    east_m = (lon - reference_lon) * 111_320.0 * cos_lat
    north_m = (lat - reference_lat) * 111_320.0
    return east_m, north_m


BASELINE_PREDICTORS = {
    "last_position": last_position_prediction,
    "constant_velocity": constant_velocity_prediction,
}

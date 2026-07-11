"""Deploy-time runtime for the exported NumPy trajectory predictor."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

import numpy as np

from ..utils import meters_to_lat_lon_delta
from .baseline_predictors import constant_velocity_prediction, latlon_to_local_m


@dataclass
class NumpySequencePredictor:
    model_type: str
    history_points: int
    future_offsets_s: List[float]
    feature_mean: List[float]
    feature_std: List[float]
    weights: List[List[float]]
    ridge: float = 0.01

    @classmethod
    def load(cls, path: Path | str) -> "NumpySequencePredictor":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            model_type=payload["model_type"],
            history_points=int(payload["history_points"]),
            future_offsets_s=[float(value) for value in payload["future_offsets_s"]],
            feature_mean=[float(value) for value in payload["feature_mean"]],
            feature_std=[float(value) for value in payload["feature_std"]],
            weights=[[float(item) for item in row] for row in payload["weights"]],
            ridge=float(payload.get("ridge", 0.01)),
        )

    def to_json_dict(self) -> Dict[str, Any]:
        return {
            "model_type": self.model_type,
            "history_points": self.history_points,
            "future_offsets_s": self.future_offsets_s,
            "feature_mean": self.feature_mean,
            "feature_std": self.feature_std,
            "weights": self.weights,
            "ridge": self.ridge,
            "feature_schema": "relative_xy_m + speed + heading_sin_cos + time_delta",
            "target_schema": "future_relative_xy_m_residual_over_constant_velocity",
            "base_predictor": "constant_velocity",
            "safety_boundary": "Trajectory prediction only; no weapon control or engagement advice.",
        }

    def predict_sample(self, sample: Dict[str, Any]) -> List[Dict[str, float]]:
        return self.predict_history(sample.get("history", []), sample.get("future", []))

    def predict_history(self, history: List[Dict[str, Any]], future_template: Iterable[Dict[str, Any] | float]) -> List[Dict[str, float]]:
        if len(history) < self.history_points:
            return []
        history_window = history[-self.history_points :]
        future_offsets = [float(item["dt_s"]) if isinstance(item, dict) else float(item) for item in future_template]
        if future_offsets != self.future_offsets_s:
            return []
        x = np.array(feature_vector(history_window, self.history_points), dtype=float)
        mean = np.array(self.feature_mean, dtype=float)
        std = np.array(self.feature_std, dtype=float)
        weights = np.array(self.weights, dtype=float)
        design = np.concatenate([[1.0], (x - mean) / std])
        residual_target = design @ weights
        anchor = history_window[-1]
        base_sample = {"history": history_window, "future": [{"dt_s": offset} for offset in self.future_offsets_s]}
        base_predictions = constant_velocity_prediction(base_sample)
        predictions = []
        for index, dt_s in enumerate(self.future_offsets_s):
            base = base_predictions[index]
            base_east_m, base_north_m = latlon_to_local_m(float(base["lat"]), float(base["lon"]), float(anchor["lat"]), float(anchor["lon"]))
            east_m = base_east_m + float(residual_target[index * 2])
            north_m = base_north_m + float(residual_target[index * 2 + 1])
            d_lat, d_lon = meters_to_lat_lon_delta(north_m, east_m, float(anchor["lat"]))
            predictions.append(
                {
                    "dt_s": dt_s,
                    "timestamp": float(anchor.get("timestamp", 0.0)) + dt_s,
                    "lat": float(anchor["lat"]) + d_lat,
                    "lon": float(anchor["lon"]) + d_lon,
                    "alt": float(anchor.get("alt", 0.0)),
                    "speed": float(anchor.get("speed", 0.0)),
                    "heading": float(anchor.get("heading", 0.0)),
                }
            )
        return predictions


def feature_vector(history: List[Dict[str, Any]], history_points: int) -> List[float]:
    if len(history) < history_points:
        raise ValueError("Not enough history points for feature extraction.")
    window = history[-history_points:]
    anchor = window[-1]
    anchor_time = float(anchor.get("timestamp", 0.0))
    values: List[float] = []
    for point in window:
        east_m, north_m = latlon_to_local_m(float(point["lat"]), float(point["lon"]), float(anchor["lat"]), float(anchor["lon"]))
        heading_rad = np.deg2rad(float(point.get("heading", 0.0)))
        values.extend(
            [
                east_m,
                north_m,
                float(point.get("speed", 0.0)),
                float(np.sin(heading_rad)),
                float(np.cos(heading_rad)),
                float(point.get("timestamp", anchor_time)) - anchor_time,
            ]
        )
    return values

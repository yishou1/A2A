"""Offline prediction evaluation for landing-demo scenarios."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from app.models import Detection, TrackState
from app.scenario_generator import generate_long_operation_sequence
from app.st_gnn_predictor import STGNNTrajectoryPredictor
from app.tracker import MultiTargetTracker
from app.utils import haversine_m


def evaluate_sequence(frames: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    frame_list = list(frames)
    baseline = _evaluate_one_mode(frame_list, use_st_gnn=False)
    enhanced = _evaluate_one_mode(frame_list, use_st_gnn=True)
    return {
        "scenario_id": "in_memory_sequence",
        "frames_processed": len(frame_list),
        "baseline": baseline,
        "enhanced": enhanced,
        "st_gnn_delta": _delta(baseline, enhanced),
        "safety_boundary": "Simulation-only prediction evaluation; no weapon control or engagement advice.",
    }


def _evaluate_one_mode(frames: List[Dict[str, Any]], use_st_gnn: bool) -> Dict[str, Any]:
    tracker = MultiTargetTracker()
    graph_predictor = STGNNTrajectoryPredictor()
    previous_predictions: Dict[str, Dict[str, Any]] = {}
    samples: List[Dict[str, float]] = []

    for frame in frames:
        detections = [Detection.model_validate(item) for item in frame.get("detections", [])]
        current_by_source_id = {
            _source_track_key(detection.detection_id): detection
            for detection in detections
        }
        for source_key, detection in current_by_source_id.items():
            previous = previous_predictions.get(source_key)
            if previous:
                error = _prediction_error(previous["predicted_path"], previous["timestamp"], detection)
                if error is not None:
                    samples.append(error)

        tracks = tracker.update(detections, algorithm_level=str(frame.get("algorithm_level", "medium")))
        if use_st_gnn:
            tracks = graph_predictor.refine(tracks)
        previous_predictions = {
            _source_track_key(str(track.metadata.get("last_detection_id", track.track_id))): {
                "timestamp": track.last_update_time,
                "predicted_path": [dict(point) for point in track.predicted_path],
            }
            for track in tracks
        }

    return _summarize(samples, "kalman_imm_st_gnn" if use_st_gnn else "kalman_imm")


def _prediction_error(predicted_path: List[Dict[str, Any]], previous_timestamp: float, detection: Detection) -> Dict[str, float] | None:
    if not predicted_path:
        return None
    elapsed = max(0.0, detection.timestamp - previous_timestamp)
    candidate = min(predicted_path, key=lambda point: abs(float(point.get("dt_s", 0.0)) - elapsed))
    error_m = haversine_m(float(candidate["lat"]), float(candidate["lon"]), detection.lat, detection.lon)
    uncertainty_radius_m = float(candidate.get("uncertainty_radius_m", 0.0) or 0.0)
    return {
        "elapsed_s": elapsed,
        "matched_dt_s": float(candidate.get("dt_s", 0.0)),
        "error_m": error_m,
        "squared_error_m2": error_m * error_m,
        "inside_uncertainty": 1.0 if uncertainty_radius_m > 0 and error_m <= uncertainty_radius_m else 0.0,
    }


def _summarize(samples: List[Dict[str, float]], mode: str) -> Dict[str, Any]:
    if not samples:
        return {
            "mode": mode,
            "sample_count": 0,
            "mean_ade_m": None,
            "mean_fde_m": None,
            "rmse_m": None,
            "uncertainty_hit_rate": None,
        }
    errors = [sample["error_m"] for sample in samples]
    fde_samples = [sample["error_m"] for sample in samples if sample["matched_dt_s"] >= 30.0] or errors
    hit_rate = sum(sample["inside_uncertainty"] for sample in samples) / len(samples)
    rmse = (sum(sample["squared_error_m2"] for sample in samples) / len(samples)) ** 0.5
    return {
        "mode": mode,
        "sample_count": len(samples),
        "mean_ade_m": round(sum(errors) / len(errors), 2),
        "mean_fde_m": round(sum(fde_samples) / len(fde_samples), 2),
        "rmse_m": round(rmse, 2),
        "uncertainty_hit_rate": round(hit_rate, 4),
    }


def _delta(baseline: Dict[str, Any], enhanced: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "mean_ade_delta_m": _metric_delta(baseline, enhanced, "mean_ade_m"),
        "mean_fde_delta_m": _metric_delta(baseline, enhanced, "mean_fde_m"),
        "rmse_delta_m": _metric_delta(baseline, enhanced, "rmse_m"),
        "interpretation": "Negative deltas mean ST-GNN enhanced prediction reduced error for this scenario.",
    }


def _metric_delta(baseline: Dict[str, Any], enhanced: Dict[str, Any], key: str) -> float | None:
    if baseline.get(key) is None or enhanced.get(key) is None:
        return None
    return round(float(enhanced[key]) - float(baseline[key]), 2)


def _source_track_key(detection_id: str) -> str:
    marker = "-f"
    if marker in detection_id:
        return detection_id.rsplit(marker, 1)[0]
    return detection_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Track Threat Agent prediction performance.")
    parser.add_argument("--frames", type=int, default=90, help="Number of deterministic scenario frames to evaluate.")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path.")
    args = parser.parse_args()

    sequence = generate_long_operation_sequence(frame_count=args.frames)
    report = evaluate_sequence(sequence["frames"])
    report["scenario_id"] = sequence["scenario_id"]
    report["scenario_name"] = sequence["scenario_name"]

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

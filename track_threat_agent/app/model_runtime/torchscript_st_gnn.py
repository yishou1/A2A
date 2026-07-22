"""Optional TorchScript ST-GNN v2 runtime with per-frame physical fallback."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import numpy as np

from ..models import TrackState
from ..utils import haversine_m


NODE_FEATURE_SCHEMA = (
    "relative_east_m",
    "relative_north_m",
    "delta_t_s",
    "speed_mps",
    "heading_sin",
    "heading_cos",
    "altitude_delta_m",
    "confidence",
    "valid_mask",
)
EDGE_FEATURE_COUNT = 8


class TorchScriptBundleRunner:
    """Load, verify and execute one `st_gnn_model_bundle/v2` directory."""

    def __init__(self, bundle_dir: Path | str | None) -> None:
        self.bundle_dir = Path(bundle_dir).expanduser() if bundle_dir else None
        self.manifest: Dict[str, Any] = {}
        self.normalization: Dict[str, Any] = {}
        self.model: Any | None = None
        self.load_error: str | None = None
        self.object_type: str | None = None
        self._load()

    @property
    def loaded(self) -> bool:
        return self.model is not None and self.load_error is None

    def status(self) -> Dict[str, Any]:
        return {
            "loaded": self.loaded,
            "load_error": self.load_error,
            "bundle_dir": str(self.bundle_dir) if self.bundle_dir else None,
            "schema_version": self.manifest.get("schema_version", "st_gnn_model_bundle/v2"),
            "model_version": self.manifest.get("model_version"),
            "object_type": self.manifest.get("object_type"),
            "release_status": self.manifest.get("release_status"),
            "release_gate_passed": (self.manifest.get("release_gate") or {}).get("passed"),
            "test_metrics": self.manifest.get("test_metrics", {}),
            "prediction_horizons_s": self.manifest.get("prediction_horizons_s", []),
        }

    def infer(
        self,
        history: np.ndarray,
        edge_index: np.ndarray,
        edge_features: np.ndarray,
        baseline: np.ndarray,
    ) -> Dict[str, Any]:
        if not self.loaded:
            raise RuntimeError(self.load_error or "TorchScript model is not loaded")
        import torch

        normalized_history = _normalize(
            history,
            self.normalization["node_mean"],
            self.normalization["node_std"],
        )
        normalized_edges = _normalize(
            edge_features,
            self.normalization["edge_mean"],
            self.normalization["edge_std"],
        )
        inputs = (
            torch.tensor(normalized_history, dtype=torch.float32),
            torch.tensor(edge_index, dtype=torch.long),
            torch.tensor(normalized_edges, dtype=torch.float32),
            torch.tensor(baseline, dtype=torch.float32),
        )
        started = time.perf_counter()
        with torch.inference_mode():
            residual, log_sigma, prediction = self.model(*inputs)
        latency_ms = (time.perf_counter() - started) * 1000.0
        return {
            "residual_mean": residual.detach().cpu().numpy(),
            "log_sigma": log_sigma.detach().cpu().numpy(),
            "prediction": prediction.detach().cpu().numpy(),
            "latency_ms": latency_ms,
        }

    def _load(self) -> None:
        if self.bundle_dir is None:
            self.load_error = "model directory is not configured"
            return
        try:
            manifest = json.loads(
                (self.bundle_dir / "model_manifest.json").read_text(encoding="utf-8")
            )
            if manifest.get("schema_version") != "st_gnn_model_bundle/v2":
                raise ValueError(f"unsupported schema {manifest.get('schema_version')}")
            if tuple(manifest.get("node_feature_schema", [])) != NODE_FEATURE_SCHEMA:
                raise ValueError("node feature schema mismatch")
            if len(manifest.get("edge_feature_schema", [])) != EDGE_FEATURE_COUNT:
                raise ValueError("edge feature schema mismatch")
            # Keep identity/schema visible in /models even if torch is absent or
            # loading fails later. The runtime status must distinguish a known
            # but unavailable bundle from an unconfigured model.
            self.manifest = manifest
            self.object_type = str(manifest["object_type"])
            self._verify_hashes()
            self.normalization = json.loads(
                (self.bundle_dir / manifest["normalization_file"]).read_text(encoding="utf-8")
            )
            import torch

            self.model = torch.jit.load(
                str(self.bundle_dir / manifest["model_file"]),
                map_location="cpu",
            ).eval()
            self._validate_golden_io()
        except Exception as exc:
            self.model = None
            self.load_error = f"{type(exc).__name__}: {exc}"

    def _verify_hashes(self) -> None:
        checksums = json.loads(
            (self.bundle_dir / "sha256sums.json").read_text(encoding="utf-8")
        )
        for name, expected in checksums.items():
            path = self.bundle_dir / name
            if not path.is_file() or _sha256(path) != expected:
                raise ValueError(f"SHA256 mismatch: {name}")

    def _validate_golden_io(self) -> None:
        golden = json.loads((self.bundle_dir / "golden_io.json").read_text(encoding="utf-8"))
        dtypes = golden["input_dtypes"]
        inputs = golden["inputs"]
        import torch

        tensors = []
        for name in (
            "history_features",
            "edge_index",
            "edge_features",
            "physics_baseline",
        ):
            dtype = torch.long if dtypes[name] == "int64" else torch.float32
            tensors.append(torch.tensor(inputs[name], dtype=dtype))
        with torch.inference_mode():
            actual = self.model(*tensors)
        expected = (
            torch.tensor(golden["outputs"]["residual_mean"]),
            torch.tensor(golden["outputs"]["log_sigma"]),
            torch.tensor(golden["outputs"]["prediction"]),
        )
        max_error = max(
            float(torch.max(torch.abs(left - right)).item())
            for left, right in zip(actual, expected)
        )
        if max_error > float(golden.get("tolerance", 1e-4)):
            raise ValueError(f"golden I/O mismatch: {max_error}")


class TrackSTGNNRuntime:
    def __init__(
        self,
        runners: Mapping[str, Any] | None = None,
        *,
        required: bool = False,
        enforce_release_gate: bool = False,
        max_inference_ms: float = 200.0,
    ) -> None:
        self.runners = dict(runners or {})
        self.required = required
        self.enforce_release_gate = enforce_release_gate
        self.max_inference_ms = max_inference_ms

    @classmethod
    def from_env(cls, default_model_root: Path | str | None = None) -> "TrackSTGNNRuntime":
        legacy = os.getenv("ST_GNN_MODEL_DIR")
        aircraft_dir = os.getenv("ST_GNN_AIRCRAFT_MODEL_DIR") or legacy
        ship_dir = os.getenv("ST_GNN_SHIP_MODEL_DIR")
        if default_model_root:
            root = Path(default_model_root).expanduser()
            aircraft_dir = aircraft_dir or _first_existing_dir(
                root,
                (
                    "st_gnn_aircraft_kaggle_v1",
                    "st_gnn_aircraft_kaggle_v1_candidate",
                    "st_gnn_aircraft_v1",
                ),
            )
            ship_dir = ship_dir or _first_existing_dir(
                root,
                (
                    "st_gnn_ship_kaggle_v1",
                    "st_gnn_ship_v1",
                ),
            )
        runners = {}
        if aircraft_dir:
            runners["aircraft"] = TorchScriptBundleRunner(aircraft_dir)
        if ship_dir:
            runners["ship"] = TorchScriptBundleRunner(ship_dir)
        return cls(
            runners,
            required=os.getenv("ST_GNN_REQUIRED", "false").lower() in {"1", "true", "yes", "on"},
            enforce_release_gate=os.getenv("ST_GNN_ENFORCE_RELEASE_GATE", "false").lower()
            in {"1", "true", "yes", "on"},
            max_inference_ms=float(os.getenv("ST_GNN_MAX_INFERENCE_MS", "200")),
        )

    @property
    def ready(self) -> bool:
        if not self.required:
            return True
        return bool(self.runners) and all(self._runner_usable(runner) for runner in self.runners.values())

    def status(self) -> Dict[str, Any]:
        statuses = {object_type: runner.status() for object_type, runner in self.runners.items()}
        loaded_count = sum(1 for value in statuses.values() if value.get("loaded"))
        usable_count = sum(1 for runner in self.runners.values() if self._runner_usable(runner))
        if self.required and not self.ready:
            overall = "unavailable"
        elif usable_count == len(statuses) and usable_count > 0:
            overall = "ready"
        elif loaded_count > 0:
            overall = "degraded"
        else:
            overall = "degraded"
        return {
            "overall": overall,
            "required": self.required,
            "enforce_release_gate": self.enforce_release_gate,
            "ready": self.ready,
            "max_inference_ms": self.max_inference_ms,
            "models": statuses,
        }

    def refine_tracks(self, tracks: Iterable[TrackState]) -> List[TrackState]:
        track_list = list(tracks)
        for object_type, runner in self.runners.items():
            candidates = [track for track in track_list if track.object_type == object_type]
            if not candidates:
                continue
            if not runner.loaded:
                self._fallback(candidates, "model_unavailable")
                continue
            if not self._runner_usable(runner):
                self._fallback(candidates, "release_gate_failed")
                continue
            self._run_group(candidates, runner)
        return track_list

    def _runner_usable(self, runner: Any) -> bool:
        if not runner.loaded:
            return False
        if not self.enforce_release_gate:
            return True
        return (runner.manifest.get("release_gate") or {}).get("passed") is True

    def _run_group(self, tracks: List[TrackState], runner: Any) -> None:
        manifest = runner.manifest
        histories = []
        eligible = []
        for track in tracks:
            features = _history_features(
                track,
                history_points=int(manifest["history_points"]),
                interval_s=float(manifest["sampling_interval_s"]),
            )
            if features is None:
                self._fallback([track], "insufficient_history")
                continue
            histories.append(features)
            eligible.append(track)
        if not eligible:
            return
        history_array = np.asarray(histories, dtype=np.float32)
        edge_index, edge_features = _build_edges(
            eligible,
            float(manifest.get("graph_thresholds", {}).get("max_edge_distance_m", 50_000.0)),
            int(manifest.get("graph_thresholds", {}).get("max_neighbors", 16)),
        )
        horizons = [float(value) for value in manifest["prediction_horizons_s"]]
        baselines = []
        baseline_names = []
        for track, history in zip(eligible, history_array):
            baseline_name, offsets = _physics_baseline(track, history, horizons)
            baseline_names.append(baseline_name)
            baselines.append(offsets)
        baseline_array = np.asarray(baselines, dtype=np.float32)
        try:
            result = runner.infer(
                history_array,
                edge_index,
                edge_features,
                baseline_array,
            )
            latency_ms = float(result["latency_ms"])
            if latency_ms > self.max_inference_ms:
                self._fallback(eligible, "inference_timeout")
                return
            prediction = np.asarray(result["prediction"], dtype=float)
            log_sigma = np.asarray(result["log_sigma"], dtype=float)
            if prediction.shape != baseline_array.shape or not np.isfinite(prediction).all():
                raise ValueError("invalid prediction shape or non-finite output")
        except Exception as exc:
            self._fallback(eligible, f"inference_error:{type(exc).__name__}")
            return
        for node_index, track in enumerate(eligible):
            self._merge_prediction(
                track,
                prediction[node_index],
                log_sigma[node_index],
                horizons,
                model_version=str(manifest["model_version"]),
                baseline_model=baseline_names[node_index],
                latency_ms=latency_ms,
                uncertainty_scale=_uncertainty_scale(manifest),
            )

    def _merge_prediction(
        self,
        track: TrackState,
        offsets: np.ndarray,
        log_sigma: np.ndarray,
        horizons: Sequence[float],
        *,
        model_version: str,
        baseline_model: str,
        latency_ms: float,
        uncertainty_scale: float,
    ) -> None:
        by_horizon = {float(point.get("dt_s", -1)): dict(point) for point in track.predicted_path}
        for index, horizon in enumerate(horizons):
            east, north = float(offsets[index][0]), float(offsets[index][1])
            lat, lon = _offset_to_latlon(track.lat, track.lon, east, north)
            sigma_east, sigma_north = np.exp(log_sigma[index]) * uncertainty_scale
            uncertainty = 1.645 * math.hypot(float(sigma_east), float(sigma_north))
            distance = max(1.0, math.hypot(east, north))
            confidence = max(
                0.05,
                min(0.99, track.track_quality * math.exp(-uncertainty / distance)),
            )
            by_horizon[horizon] = {
                "dt_s": horizon,
                "timestamp": track.last_update_time + horizon,
                "lat": lat,
                "lon": lon,
                "alt": track.alt,
                "speed": track.speed,
                "heading": track.heading,
                "model_used": "st_gnn_torchscript",
                "prediction_model": "st_gnn_torchscript",
                "model_version": model_version,
                "baseline_model": baseline_model,
                "prediction_confidence": round(confidence, 4),
                "uncertainty_radius_m": round(uncertainty, 3),
                "uncertainty_calibration_scale": round(uncertainty_scale, 6),
                "inference_latency_ms": round(latency_ms, 3),
                "fallback_reason": None,
            }
        track.predicted_path = [by_horizon[key] for key in sorted(by_horizon)]
        track.metadata["st_gnn_runtime"] = {
            "applied": True,
            "runtime": "torchscript_pytorch",
            "model_version": model_version,
            "baseline_model": baseline_model,
            "prediction_horizons_s": list(horizons),
            "inference_latency_ms": round(latency_ms, 3),
            "uncertainty_calibration_scale": round(uncertainty_scale, 6),
            "fallback_reason": None,
        }

    def _fallback(self, tracks: Iterable[TrackState], reason: str) -> None:
        for track in tracks:
            for point in track.predicted_path:
                point.setdefault("model_version", None)
                point.setdefault("baseline_model", point.get("model_used", "physical"))
                point.setdefault("inference_latency_ms", None)
                point["fallback_reason"] = reason
            track.metadata["st_gnn_runtime"] = {
                "applied": False,
                "runtime": "physical_fallback",
                "model_version": None,
                "fallback_reason": reason,
            }


def _history_features(
    track: TrackState,
    *,
    history_points: int,
    interval_s: float,
) -> np.ndarray | None:
    points = sorted(track.history_path, key=lambda item: float(item.get("timestamp", 0.0)))
    if len(points) < history_points:
        return None
    current = points[-1]
    targets = [
        float(current["timestamp"]) - interval_s * (history_points - 1 - index)
        for index in range(history_points)
    ]
    selected = []
    for target in targets:
        nearest = min(points, key=lambda point: abs(float(point["timestamp"]) - target))
        if abs(float(nearest["timestamp"]) - target) > interval_s * 0.35:
            return None
        selected.append(nearest)
    if len({float(point["timestamp"]) for point in selected}) != history_points:
        return None
    rows = []
    for point in selected:
        east, north = _relative_xy(
            float(current["lat"]),
            float(current["lon"]),
            float(point["lat"]),
            float(point["lon"]),
        )
        heading = math.radians(float(point.get("heading", track.heading)))
        rows.append(
            [
                east,
                north,
                float(point["timestamp"]) - float(current["timestamp"]),
                float(point.get("speed", track.speed)),
                math.sin(heading),
                math.cos(heading),
                float(point.get("alt", track.alt)) - float(current.get("alt", track.alt)),
                float(point.get("confidence", track.track_quality)),
                1.0,
            ]
        )
    return np.asarray(rows, dtype=np.float32)


def _build_edges(
    tracks: Sequence[TrackState],
    max_distance_m: float,
    max_neighbors: int,
) -> Tuple[np.ndarray, np.ndarray]:
    outgoing: Dict[int, List[Tuple[float, int, List[float]]]] = {}
    for source_index, source in enumerate(tracks):
        outgoing[source_index] = []
        for target_index, target in enumerate(tracks):
            if source_index == target_index:
                continue
            distance = haversine_m(source.lat, source.lon, target.lat, target.lon)
            if distance > max_distance_m:
                continue
            east, north = _relative_xy(source.lat, source.lon, target.lat, target.lon)
            bearing = math.atan2(east, north)
            heading_delta = math.radians(
                ((target.heading - source.heading + 180.0) % 360.0) - 180.0
            )
            outgoing[source_index].append(
                (
                    distance,
                    target_index,
                    [
                        distance,
                        math.sin(bearing),
                        math.cos(bearing),
                        math.sin(heading_delta),
                        math.cos(heading_delta),
                        target.speed - source.speed,
                        target.alt - source.alt,
                        max(0.0, 1.0 - distance / max(max_distance_m, 1.0)),
                    ],
                )
            )
    pairs = []
    features = []
    for source_index in sorted(outgoing):
        for _, target_index, values in sorted(
            outgoing[source_index],
            key=lambda item: item[0],
        )[:max_neighbors]:
            pairs.append([source_index, target_index])
            features.append(values)
    if not pairs:
        return np.zeros((2, 0), dtype=np.int64), np.zeros((0, 8), dtype=np.float32)
    return np.asarray(pairs, dtype=np.int64).T, np.asarray(features, dtype=np.float32)


def _physics_baseline(
    track: TrackState,
    history: np.ndarray,
    horizons: Sequence[float],
) -> Tuple[str, np.ndarray]:
    speed = float(history[-1, 3])
    heading = math.atan2(float(history[-1, 4]), float(history[-1, 5]))
    elapsed = max(1e-6, float(history[-1, 2] - history[-2, 2]))
    previous_heading = math.atan2(float(history[-2, 4]), float(history[-2, 5]))
    turn_rate = ((heading - previous_heading + math.pi) % (2 * math.pi) - math.pi) / elapsed
    acceleration = (float(history[-1, 3]) - float(history[-2, 3])) / elapsed
    cv_offsets = []
    turn_offsets = []
    ctra_offsets = []
    for horizon in horizons:
        cv_offsets.append(
            [
                speed * math.sin(heading) * horizon,
                speed * math.cos(heading) * horizon,
            ]
        )
        if abs(turn_rate) < 1e-7:
            turn_offsets.append(cv_offsets[-1])
        else:
            end_heading = heading + turn_rate * horizon
            turn_offsets.append(
                [
                    speed / turn_rate * (math.cos(heading) - math.cos(end_heading)),
                    speed / turn_rate * (math.sin(end_heading) - math.sin(heading)),
                ]
            )
        steps = max(1, int(math.ceil(horizon)))
        dt = horizon / steps
        east = 0.0
        north = 0.0
        for step in range(steps):
            at = (step + 0.5) * dt
            step_heading = heading + turn_rate * at
            step_speed = max(0.0, speed + acceleration * at)
            east += step_speed * math.sin(step_heading) * dt
            north += step_speed * math.cos(step_heading) * dt
        ctra_offsets.append([east, north])
    cv = np.asarray(cv_offsets, dtype=np.float32)
    coordinated_turn = np.asarray(turn_offsets, dtype=np.float32)
    ctra = np.asarray(ctra_offsets, dtype=np.float32)
    if track.object_type == "aircraft":
        maneuver_strength = min(
            1.0,
            abs(turn_rate) / math.radians(3.0) + abs(acceleration) / 8.0,
        )
        return "adaptive_ctra_fusion", (
            (1.0 - maneuver_strength) * cv + maneuver_strength * ctra
        ).astype(np.float32)
    turn_strength = min(1.0, abs(turn_rate) / math.radians(0.05))
    return "coordinated_turn_cv", (
        (1.0 - turn_strength) * cv + turn_strength * coordinated_turn
    ).astype(np.float32)


def _normalize(values: np.ndarray, mean: Sequence[float], std: Sequence[float]) -> np.ndarray:
    mean_array = np.asarray(mean, dtype=np.float32)
    std_array = np.maximum(np.asarray(std, dtype=np.float32), 1e-6)
    return (values - mean_array) / std_array


def _uncertainty_scale(manifest: Mapping[str, Any]) -> float:
    calibration = (manifest.get("test_metrics") or {}).get(
        "uncertainty_calibration",
        {},
    )
    try:
        scale = float(calibration.get("sigma_scale", 1.0))
    except (TypeError, ValueError):
        return 1.0
    return scale if math.isfinite(scale) and scale > 0.0 else 1.0


def _relative_xy(anchor_lat: float, anchor_lon: float, lat: float, lon: float) -> Tuple[float, float]:
    north = (lat - anchor_lat) * 111_320.0
    east = (lon - anchor_lon) * 111_320.0 * max(0.01, math.cos(math.radians(anchor_lat)))
    return east, north


def _offset_to_latlon(lat: float, lon: float, east: float, north: float) -> Tuple[float, float]:
    return (
        lat + north / 111_320.0,
        lon + east / (111_320.0 * max(0.01, math.cos(math.radians(lat)))),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _first_existing_dir(root: Path, names: Sequence[str]) -> Path | None:
    for name in names:
        candidate = root / name
        if candidate.is_dir():
            return candidate
    return None

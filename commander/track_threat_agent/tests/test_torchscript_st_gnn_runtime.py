import numpy as np
import hashlib
import json
import pytest

from app.model_runtime.torchscript_st_gnn import (
    NODE_FEATURE_SCHEMA,
    TorchScriptBundleRunner,
    TrackSTGNNRuntime,
    _history_features,
    _physics_baseline,
)
from app.models import TrackState


class FakeRunner:
    def __init__(
        self,
        object_type,
        horizons,
        release_gate_passed=True,
        uncertainty_scale=1.0,
    ):
        self.object_type = object_type
        self.manifest = {
            "schema_version": "st_gnn_model_bundle/v2",
            "model_version": f"{object_type}-v1",
            "object_type": object_type,
            "sampling_interval_s": 10 if object_type == "aircraft" else 600,
            "history_points": 6,
            "prediction_horizons_s": horizons,
            "node_feature_schema": [
                "relative_east_m",
                "relative_north_m",
                "delta_t_s",
                "speed_mps",
                "heading_sin",
                "heading_cos",
                "altitude_delta_m",
                "confidence",
                "valid_mask",
            ],
            "edge_feature_schema": [f"edge-{index}" for index in range(8)],
            "graph_thresholds": {"max_edge_distance_m": 50_000},
            "release_gate": {"passed": release_gate_passed},
            "test_metrics": {
                "uncertainty_calibration": {"sigma_scale": uncertainty_scale}
            },
        }
        self.loaded = True
        self.load_error = None

    def infer(self, history, edge_index, edge_features, baseline):
        residual = np.zeros_like(baseline)
        residual[..., 0] = 25.0
        log_sigma = np.zeros_like(baseline) + np.log(20.0)
        return {
            "residual_mean": residual,
            "log_sigma": log_sigma,
            "prediction": baseline + residual,
            "latency_ms": 12.5,
        }

    def status(self):
        return {
            "loaded": self.loaded,
            "load_error": self.load_error,
            "object_type": self.object_type,
            "model_version": self.manifest["model_version"],
        }


def _track(object_type, track_id, interval, lon_offset=0.0):
    history = []
    for index in range(6):
        history.append(
            {
                "timestamp": 1_700_000_000 + index * interval,
                "lat": 31.0,
                "lon": 121.0 + lon_offset + index * 0.001,
                "alt": 5_000.0 if object_type == "aircraft" else 0.0,
                "speed": 100.0 if object_type == "aircraft" else 8.0,
                "heading": 90.0,
                "confidence": 0.95,
            }
        )
    return TrackState(
        track_id=track_id,
        object_type=object_type,
        lat=history[-1]["lat"],
        lon=history[-1]["lon"],
        alt=history[-1]["alt"],
        speed=history[-1]["speed"],
        heading=history[-1]["heading"],
        vx=history[-1]["speed"],
        vy=0.0,
        track_quality=0.95,
        last_update_time=history[-1]["timestamp"],
        history_path=history,
        predicted_path=[
            {
                "dt_s": horizon,
                "timestamp": history[-1]["timestamp"] + horizon,
                "lat": history[-1]["lat"],
                "lon": history[-1]["lon"] + horizon * 0.00001,
                "model_used": "adaptive_multi_model_fused",
                "prediction_confidence": 0.8,
                "uncertainty_radius_m": 100.0,
            }
            for horizon in (10.0, 20.0, 30.0, 60.0, 120.0)
        ],
    )


def test_aircraft_runtime_replaces_model_horizons_and_keeps_120_second_fallback():
    runtime = TrackSTGNNRuntime(
        runners={"aircraft": FakeRunner("aircraft", [10, 20, 30, 60])},
        max_inference_ms=200,
    )
    tracks = [_track("aircraft", "A1", 10, 0.0), _track("aircraft", "A2", 10, 0.005)]

    runtime.refine_tracks(tracks)

    track = tracks[0]
    by_horizon = {point["dt_s"]: point for point in track.predicted_path}
    assert by_horizon[10.0]["model_used"] == "st_gnn_torchscript"
    assert by_horizon[10.0]["model_version"] == "aircraft-v1"
    assert by_horizon[10.0]["baseline_model"] == "adaptive_ctra_fusion"
    assert by_horizon[10.0]["uncertainty_radius_m"] > 0
    assert by_horizon[10.0]["inference_latency_ms"] == 12.5
    assert by_horizon[120.0]["model_used"] == "adaptive_multi_model_fused"
    assert track.metadata["st_gnn_runtime"]["applied"] is True


def test_ship_runtime_appends_long_horizons_and_preserves_short_term_predictions():
    runtime = TrackSTGNNRuntime(
        runners={"ship": FakeRunner("ship", [600, 1200])},
        max_inference_ms=200,
    )
    track = _track("ship", "S1", 600)

    runtime.refine_tracks([track])

    horizons = {point["dt_s"] for point in track.predicted_path}
    assert {10.0, 20.0, 30.0, 60.0, 120.0, 600.0, 1200.0} <= horizons
    assert next(point for point in track.predicted_path if point["dt_s"] == 600.0)["baseline_model"] == "coordinated_turn_cv"


def test_runtime_applies_bundle_uncertainty_calibration_scale():
    runtime = TrackSTGNNRuntime(
        runners={
            "aircraft": FakeRunner(
                "aircraft",
                [10, 20, 30, 60],
                uncertainty_scale=0.5,
            )
        },
    )
    track = _track("aircraft", "A1", 10)

    runtime.refine_tracks([track])

    point = next(item for item in track.predicted_path if item["dt_s"] == 10.0)
    expected_radius = 1.645 * np.hypot(10.0, 10.0)
    assert point["uncertainty_radius_m"] == pytest.approx(expected_radius, abs=0.001)
    assert point["uncertainty_calibration_scale"] == 0.5


def test_runtime_marks_insufficient_history_without_interrupting_agent():
    runtime = TrackSTGNNRuntime(
        runners={"aircraft": FakeRunner("aircraft", [10, 20, 30, 60])},
        max_inference_ms=200,
    )
    track = _track("aircraft", "A1", 10)
    track.history_path = track.history_path[-2:]

    runtime.refine_tracks([track])

    assert track.metadata["st_gnn_runtime"]["applied"] is False
    assert track.metadata["st_gnn_runtime"]["fallback_reason"] == "insufficient_history"
    assert all(point["model_used"] == "adaptive_multi_model_fused" for point in track.predicted_path)


def test_release_gate_enforcement_rejects_candidate_and_falls_back():
    runner = FakeRunner("aircraft", [10, 20, 30, 60], release_gate_passed=False)
    runtime = TrackSTGNNRuntime(
        runners={"aircraft": runner},
        required=False,
        enforce_release_gate=True,
    )
    track = _track("aircraft", "A1", 10)

    runtime.refine_tracks([track])

    assert runtime.ready is True
    assert runtime.status()["overall"] == "degraded"
    assert track.metadata["st_gnn_runtime"]["applied"] is False
    assert track.metadata["st_gnn_runtime"]["fallback_reason"] == "release_gate_failed"


def test_required_runtime_is_not_ready_when_release_gate_fails():
    runner = FakeRunner("aircraft", [10, 20, 30, 60], release_gate_passed=False)
    runtime = TrackSTGNNRuntime(
        runners={"aircraft": runner},
        required=True,
        enforce_release_gate=True,
    )

    assert runtime.ready is False
    assert runtime.status()["overall"] == "unavailable"


def test_online_physics_baseline_matches_constant_motion_case():
    track = _track("aircraft", "A1", 10)
    history = _history_features(track, history_points=6, interval_s=10)

    name, offsets = _physics_baseline(track, history, [10, 20])

    assert name == "adaptive_ctra_fusion"
    assert offsets[0, 0] == pytest.approx(1_000.0, rel=0.01)
    assert offsets[0, 1] == pytest.approx(0.0, abs=0.01)


def test_real_torchscript_bundle_runner_validates_and_executes_v2_bundle(tmp_path):
    torch = pytest.importorskip("torch")

    class IdentityResidual(torch.nn.Module):
        def forward(self, history, edge_index, edge_features, baseline):
            residual = torch.zeros_like(baseline)
            log_sigma = torch.zeros_like(baseline)
            return residual, log_sigma, baseline

    inputs = (
        torch.zeros((1, 6, 9)),
        torch.zeros((2, 0), dtype=torch.long),
        torch.zeros((0, 8)),
        torch.zeros((1, 4, 2)),
    )
    model = torch.jit.trace(IdentityResidual(), inputs)
    model.save(str(tmp_path / "model.ts"))
    manifest = {
        "schema_version": "st_gnn_model_bundle/v2",
        "model_version": "fixture-v1",
        "object_type": "aircraft",
        "sampling_interval_s": 10,
        "history_points": 6,
        "prediction_horizons_s": [10, 20, 30, 60],
        "node_feature_schema": list(NODE_FEATURE_SCHEMA),
        "edge_feature_schema": [f"e{index}" for index in range(8)],
        "graph_thresholds": {"max_edge_distance_m": 50_000},
        "model_file": "model.ts",
        "normalization_file": "normalization.json",
        "metrics_file": "metrics.json",
        "golden_io_file": "golden_io.json",
    }
    (tmp_path / "model_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "normalization.json").write_text(
        json.dumps({"node_mean": [0] * 9, "node_std": [1] * 9, "edge_mean": [0] * 8, "edge_std": [1] * 8}),
        encoding="utf-8",
    )
    (tmp_path / "metrics.json").write_text("{}", encoding="utf-8")
    (tmp_path / "golden_io.json").write_text(
        json.dumps(
            {
                "inputs": {
                    "history_features": inputs[0].tolist(),
                    "edge_index": inputs[1].tolist(),
                    "edge_features": inputs[2].tolist(),
                    "physics_baseline": inputs[3].tolist(),
                },
                "input_dtypes": {
                    "history_features": "float32",
                    "edge_index": "int64",
                    "edge_features": "float32",
                    "physics_baseline": "float32",
                },
                "outputs": {
                    "residual_mean": inputs[3].tolist(),
                    "log_sigma": inputs[3].tolist(),
                    "prediction": inputs[3].tolist(),
                },
                "tolerance": 0.0001,
            }
        ),
        encoding="utf-8",
    )
    names = ["model.ts", "model_manifest.json", "normalization.json", "metrics.json", "golden_io.json"]
    checksums = {
        name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
        for name in names
    }
    (tmp_path / "sha256sums.json").write_text(json.dumps(checksums), encoding="utf-8")

    runner = TorchScriptBundleRunner(tmp_path)
    result = runner.infer(
        np.zeros((1, 6, 9), dtype=np.float32),
        np.zeros((2, 0), dtype=np.int64),
        np.zeros((0, 8), dtype=np.float32),
        np.ones((1, 4, 2), dtype=np.float32),
    )

    assert runner.loaded is True
    assert np.allclose(result["prediction"], 1.0)

from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1]


def test_agent_runtime_does_not_import_training_package():
    source = (AGENT_DIR / "app" / "learned_predictor.py").read_text(encoding="utf-8")

    assert "from training." not in source
    assert "import training." not in source


def test_numpy_sequence_runtime_is_owned_by_agent():
    from app.model_runtime.numpy_sequence_predictor import NumpySequencePredictor

    assert NumpySequencePredictor.__name__ == "NumpySequencePredictor"


def test_missing_st_gnn_bundle_falls_back_without_crashing(tmp_path):
    from app.model_runtime.model_bundle import ModelBundleLoader

    status = ModelBundleLoader(tmp_path).status()

    assert status["loaded"] is False
    assert status["schema_version"] == "st_gnn_model_bundle/v1"
    assert "model_manifest.json" in status["load_error"]


def test_valid_st_gnn_bundle_manifest_is_discoverable(tmp_path):
    import json

    from app.model_runtime.model_bundle import ModelBundleLoader

    (tmp_path / "model.pt").write_bytes(b"test-weight-placeholder")
    (tmp_path / "normalization.json").write_text("{}", encoding="utf-8")
    (tmp_path / "metrics.json").write_text("{}", encoding="utf-8")
    (tmp_path / "model_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "st_gnn_model_bundle/v1",
                "model_type": "st_gnn",
                "model_version": "1.0.0",
                "object_type": "aircraft",
                "framework": "pytorch",
                "history_points": 10,
                "prediction_horizons_s": [10, 20, 30, 60],
                "node_feature_schema": ["lat", "lon", "speed", "heading"],
                "edge_feature_schema": ["distance", "heading_delta"],
                "weights_file": "model.pt",
                "normalization_file": "normalization.json",
                "metrics_file": "metrics.json",
            }
        ),
        encoding="utf-8",
    )

    status = ModelBundleLoader(tmp_path).status()

    assert status["loaded"] is True
    assert status["model_version"] == "1.0.0"
    assert status["object_type"] == "aircraft"


def test_agent_model_status_exposes_external_st_gnn_bundle():
    from app import main

    status = main._model_status()

    assert "st_gnn_model_bundle" in status
    assert status["st_gnn_model_bundle"]["schema_version"] == "st_gnn_model_bundle/v1"


def test_default_embedded_st_gnn_v2_bundles_are_discoverable(monkeypatch):
    import pytest

    pytest.importorskip("torch")
    monkeypatch.delenv("ST_GNN_MODEL_DIR", raising=False)
    monkeypatch.delenv("ST_GNN_AIRCRAFT_MODEL_DIR", raising=False)
    monkeypatch.delenv("ST_GNN_SHIP_MODEL_DIR", raising=False)

    from app.main import BACKEND_DIR
    from app.model_runtime import TrackSTGNNRuntime

    runtime = TrackSTGNNRuntime.from_env(BACKEND_DIR / "models" / "track_threat")
    status = runtime.status()

    assert status["ready"] is True
    assert status["models"]["aircraft"]["loaded"] is True
    assert status["models"]["aircraft"]["model_version"] == "st_gnn_aircraft_kaggle_v1_candidate"
    assert status["models"]["ship"]["loaded"] is True
    assert status["models"]["ship"]["model_version"] == "st_gnn_ship_kaggle_v1"

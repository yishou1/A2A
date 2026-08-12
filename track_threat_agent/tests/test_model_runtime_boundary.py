from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1]


def test_agent_runtime_does_not_ship_legacy_untrained_predictors():
    assert not (AGENT_DIR / "app" / "learned_predictor.py").exists()
    assert not (AGENT_DIR / "app" / "st_gnn_predictor.py").exists()
    assert not (AGENT_DIR / "app" / "model_runtime" / "numpy_sequence_predictor.py").exists()


def test_agent_model_status_exposes_only_current_st_gnn_runtime():
    from app import main

    status = main._model_status()

    assert "st_gnn_model_bundle" not in status
    assert status["st_gnn_runtime"]["models"]


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
    assert status["models"]["aircraft"]["model_version"] == "st_gnn_aircraft_kaggle_v1"
    assert status["models"]["ship"]["loaded"] is True
    assert status["models"]["ship"]["model_version"] == "st_gnn_ship_kaggle_v1"

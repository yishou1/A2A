from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.a2a_algorithms_common.motion_prediction import (
    fit_linear,
    motion_predictor_identity,
    motion_predictor_loaded,
    predict_single_track,
)
from services.a2a_algorithms_common.service_predictors import (
    predict_trajectory_linear_predictor,
)


ROOT = Path(__file__).resolve().parents[2]


def test_ordinary_least_squares_parameters_are_exposed() -> None:
    result = predict_single_track(
        {
            "track_id": "linear",
            "history": [
                {"t": 2.0, "x": 5.0, "y": -1.0},
                {"t": 0.0, "x": 1.0, "y": 3.0},
                {"t": 1.0, "x": 3.0, "y": 1.0},
            ],
            "weapon_prep_sec": 0.5,
            "flight_time_sec": 0.5,
        }
    )
    assert result["ok"] is True
    assert result["velocity"] == {"vx": 2.0, "vy": -2.0}
    assert result["aim_point"] == {"x": 7.0, "y": -3.0}
    assert result["fit"]["x"] == {
        "slope": 2.0,
        "intercept": 1.0,
        "r_squared": 1.0,
    }
    assert result["fit"]["y"] == {
        "slope": -2.0,
        "intercept": 3.0,
        "r_squared": 1.0,
    }


def test_duplicate_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="distinct timestamps"):
        fit_linear([1.0, 1.0], [2.0, 3.0])


def test_zero_prediction_horizon_is_preserved() -> None:
    result = predict_single_track(
        {
            "history": [
                {"t": 0.0, "x": 1.0, "y": 2.0},
                {"t": 1.0, "x": 3.0, "y": 4.0},
            ],
            "weapon_prep_sec": 0.0,
            "flight_time_sec": 0.0,
        }
    )
    assert result["future_t"] == 1.0
    assert result["aim_point"] == {"x": 3.0, "y": 4.0}


def test_reference_evaluation_and_source_identity_are_verified() -> None:
    metadata = json.loads(
        (ROOT / "models/trajectory_linear_predictor.metadata.json").read_text(
            encoding="utf-8"
        )
    )
    assert motion_predictor_loaded() is True
    assert motion_predictor_identity()["implementation_sha256"] == metadata[
        "implementation_sha256"
    ]
    evaluation = metadata["evaluation"]
    assert evaluation["test_count"] == 160
    assert evaluation["position_mae"] == 0.519819
    assert evaluation["position_rmse"] == 0.964136
    assert evaluation["position_p95"] == 2.170402


def test_service_predictor_returns_fit_and_model_identity() -> None:
    outputs = predict_trajectory_linear_predictor(
        {
            "track": {
                "track_id": "T-001",
                "history": [
                    {"t": 0.0, "x": 10.0, "y": 18.0},
                    {"t": 0.1, "x": 10.4, "y": 18.6},
                    {"t": 0.2, "x": 10.9, "y": 19.1},
                    {"t": 0.3, "x": 11.3, "y": 19.7},
                    {"t": 0.4, "x": 11.8, "y": 20.2},
                ],
                "weapon_prep_sec": 2.0,
                "flight_time_sec": 4.0,
            }
        },
        {},
    )
    assert outputs["fit"]["x"]["slope"] == 4.5
    assert outputs["fit"]["y"]["slope"] == 5.5
    assert outputs["model_identity"]["model_family"] == (
        "per_request_ordinary_least_squares_2d"
    )

import json

import pytest

from app.dbn_threat_evaluator import DBNThreatEvaluator
from app.models import TrackState


def make_track():
    return TrackState(
        track_id="trk-dbn",
        object_type="uav",
        lat=31.0,
        lon=121.0,
        alt=1000,
        speed=60,
        heading=180,
        vx=0,
        vy=-60,
        track_quality=0.9,
        last_update_time=1000.0,
        history_path=[],
        predicted_path=[],
        metadata={},
    )


def test_dbn_high_probability_rises_with_repeated_elevated_observations():
    evaluator = DBNThreatEvaluator()
    track = make_track()
    factors = {
        "distance_factor": 0.9,
        "closing_factor": 0.9,
        "type_factor": 0.7,
        "anomaly_factor": 0.4,
        "quality_factor": 0.9,
    }

    first = evaluator.update(track, 0.75, factors)
    second = evaluator.update(track, 0.80, factors)

    assert second["posterior"]["high"] >= first["posterior"]["high"]
    assert second["smoothed_score"] >= first["smoothed_score"]
    assert second["risk_pattern_probabilities"]["protected_zone_approach"] > 0
    assert second["dominant_risk_pattern"] in second["risk_pattern_probabilities"]
    assert second["parameter_model"]["model_version"] == "dbn-risk-attention-v1"
    assert len(second["parameter_model"]["sha256"]) == 64


def test_dbn_reset_clears_state():
    evaluator = DBNThreatEvaluator()
    track = make_track()
    factors = {
        "distance_factor": 0.9,
        "closing_factor": 0.9,
        "type_factor": 0.7,
        "anomaly_factor": 0.4,
        "quality_factor": 0.9,
    }
    elevated = evaluator.update(track, 0.8, factors)
    evaluator.reset()
    after_reset = evaluator.update(track, 0.8, factors)

    assert after_reset["prior"]["high"] < elevated["posterior"]["high"]


def test_dbn_risk_pattern_probability_reflects_asset_approach_factors():
    evaluator = DBNThreatEvaluator()
    track = make_track()
    factors = {
        "distance_factor": 0.95,
        "closing_factor": 0.92,
        "type_factor": 0.7,
        "anomaly_factor": 0.1,
        "quality_factor": 0.9,
    }

    result = evaluator.update(track, 0.76, factors)

    assert result["risk_pattern_probabilities"]["protected_zone_approach"] == max(result["risk_pattern_probabilities"].values())
    assert result["dbn_posterior"] == result["posterior"]
    assert result["risk_pattern_model"]["dominant_pattern"] == result["dominant_risk_pattern"]
    assert result["risk_pattern_factor"] > 0.2


def test_dbn_reports_observation_reliability_and_state_transition_delta():
    evaluator = DBNThreatEvaluator()
    track = make_track()
    factors = {
        "distance_factor": 0.95,
        "closing_factor": 0.92,
        "type_factor": 0.7,
        "anomaly_factor": 0.1,
        "quality_factor": 0.95,
    }

    first = evaluator.update(track, 0.78, factors)
    second = evaluator.update(track, 0.82, factors)

    assert second["observation_reliability"] > 0.5
    assert "transition_matrix" in second
    assert second["state_transition"]["high_delta"] >= 0
    assert second["posterior_entropy"] >= 0
    assert second["risk_pattern_transition"]["dominant_changed"] in {True, False}
    assert second["risk_pattern_model"]["utility_scores"]


def test_dbn_patterns_are_observable_situation_patterns_not_intent_labels():
    evaluator = DBNThreatEvaluator()
    result = evaluator.update(
        make_track(),
        0.6,
        {
            "distance_factor": 0.7,
            "closing_factor": 0.6,
            "type_factor": 0.7,
            "anomaly_factor": 0.2,
            "quality_factor": 0.9,
        },
    )

    assert set(result["risk_pattern_probabilities"]) == {
        "protected_zone_approach",
        "sustained_presence",
        "coordinated_motion",
        "anomalous_motion",
        "non_closing_motion",
    }
    serialized = json.dumps(result, ensure_ascii=False).lower()
    assert "surveillance" not in serialized
    assert "probe" not in serialized
    assert "intent" not in serialized


def test_dbn_rejects_invalid_parameter_file(tmp_path):
    parameter_path = tmp_path / "bad-dbn.json"
    parameter_path.write_text(
        json.dumps(
            {
                "schema_version": "dbn_risk_model/v1",
                "model_version": "broken",
                "states": ["low", "medium", "high"],
                "initial_state": {"low": 0.5, "medium": 0.4, "high": 0.4},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="DBN parameter"):
        DBNThreatEvaluator(parameter_path=parameter_path)

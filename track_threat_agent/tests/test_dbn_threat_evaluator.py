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
        metadata={"st_gnn_inspired": {"graph_influence": 0.4}},
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
    assert second["coa_probabilities"]["asset_approach"] > 0
    assert second["dominant_coa"] in second["coa_probabilities"]


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


def test_dbn_coa_probability_reflects_asset_approach_factors():
    evaluator = DBNThreatEvaluator()
    track = make_track()
    factors = {
        "distance_factor": 0.95,
        "closing_factor": 0.92,
        "type_factor": 0.7,
        "anomaly_factor": 0.1,
        "quality_factor": 0.9,
        "semantic_factor": 0.85,
        "intent_asset_approach_prob": 0.7,
        "intent_surveillance_prob": 0.1,
        "intent_formation_prob": 0.1,
        "intent_anomalous_maneuver_prob": 0.1,
    }

    result = evaluator.update(track, 0.76, factors)

    assert result["coa_probabilities"]["asset_approach"] == max(result["coa_probabilities"].values())
    assert result["coa_risk_factor"] > 0.2

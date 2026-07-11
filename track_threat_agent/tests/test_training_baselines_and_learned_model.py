from pathlib import Path

from app.learned_predictor import LearnedTrajectoryPredictor
from app.models import TrackState


MODEL_PATH = (
    Path(__file__).resolve().parents[1]
    / "models"
    / "trajectory_predictor_aircraft_short.json"
)


def test_learned_predictor_loads_exported_model_and_refines_track():
    predictor = LearnedTrajectoryPredictor(MODEL_PATH)
    track = TrackState(
        track_id="trk-test",
        object_type="aircraft",
        lat=31.002,
        lon=121.004,
        alt=10_000.0,
        speed=220.0,
        heading=60.0,
        vx=0.0,
        vy=0.0,
        track_quality=0.9,
        last_update_time=20.0,
        missed_count=0,
        history_path=[
            {
                "timestamp": 0.0,
                "lat": 31.0,
                "lon": 121.0,
                "alt": 10_000.0,
                "speed": 220.0,
                "heading": 60.0,
            },
            {
                "timestamp": 10.0,
                "lat": 31.001,
                "lon": 121.002,
                "alt": 10_000.0,
                "speed": 220.0,
                "heading": 60.0,
            },
            {
                "timestamp": 20.0,
                "lat": 31.002,
                "lon": 121.004,
                "alt": 10_000.0,
                "speed": 220.0,
                "heading": 60.0,
            },
        ],
        predicted_path=[
            {
                "dt_s": dt_s,
                "timestamp": 20.0 + dt_s,
                "lat": 31.002,
                "lon": 121.004,
                "alt": 10_000.0,
                "speed": 220.0,
                "heading": 60.0,
            }
            for dt_s in (10.0, 20.0, 30.0, 60.0)
        ],
        metadata={},
    )

    refined = predictor.refine_tracks([track])[0]

    assert predictor.loaded is True
    assert refined.predicted_path[0]["model_used"] == "learned_numpy_sequence_predictor"
    assert refined.predicted_path[0]["learned_model"]["loaded"] is True
    assert refined.metadata["learned_predictor"]["model_type"] == "numpy_ridge_sequence_predictor"

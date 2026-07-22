import json
from pathlib import Path

from app.models import Detection
from app.tracker import MultiTargetTracker
from app.utils import project_position, speed_heading_to_velocity


DATA_DIR = Path(__file__).resolve().parents[1] / "sample_data"


def load_detections(filename: str):
    payload = json.loads((DATA_DIR / filename).read_text())
    return [Detection.model_validate(item) for item in payload["detections"]]


def test_first_frame_creates_multiple_tracks():
    tracker = MultiTargetTracker()
    tracks = tracker.update(load_detections("frame_1.json"))
    assert len(tracks) == 7
    assert {track.object_type for track in tracks} >= {"aircraft", "ship", "uav", "unknown"}
    assert all(len(track.predicted_path) == 5 for track in tracks)
    assert all([point["dt_s"] for point in track.predicted_path] == [10.0, 20.0, 30.0, 60.0, 120.0] for track in tracks)


def test_second_frame_updates_existing_tracks_instead_of_creating_all_new():
    tracker = MultiTargetTracker()
    first_tracks = tracker.update(load_detections("frame_1.json"))
    first_ids = {track.track_id for track in first_tracks}

    second_tracks = tracker.update(load_detections("frame_2.json"))
    second_ids = {track.track_id for track in second_tracks}

    assert len(second_tracks) == 7
    assert second_ids == first_ids
    assert all(len(track.history_path) == 2 for track in second_tracks)


def test_history_path_does_not_exceed_50_points():
    tracker = MultiTargetTracker()
    for idx in range(65):
        detection = Detection(
            detection_id=f"det-loop-{idx}",
            object_type="aircraft",
            timestamp=float(idx),
            lat=31.0 + idx * 0.0001,
            lon=121.0 + idx * 0.0001,
            alt=5000,
            speed=20,
            heading=45,
            confidence=0.9,
            source_agent="unit-test",
        )
        tracks = tracker.update([detection])

    assert len(tracks) == 1
    assert len(tracks[0].history_path) == 50


def test_prediction_uses_adaptive_motion_profile_metadata():
    tracker = MultiTargetTracker()
    lat, lon = 31.0, 121.0
    headings = [80.0, 90.0, 100.0, 110.0]
    speed = 80.0

    tracks = []
    for idx, heading in enumerate(headings):
        if idx > 0:
            vx, vy = speed_heading_to_velocity(speed, heading)
            lat, lon = project_position(lat, lon, vx, vy, 10.0)
        tracks = tracker.update(
            [
                Detection(
                    detection_id=f"turning-{idx}",
                    object_type="uav",
                    timestamp=float(idx * 10),
                    lat=lat,
                    lon=lon,
                    alt=1200.0,
                    speed=speed,
                    heading=heading,
                    confidence=0.95,
                    source_agent="unit-test",
                )
            ]
        )

    track = tracks[0]
    assert len(track.predicted_path) == 5
    assert track.metadata["prediction"]["model"] == "adaptive_ctra_turn"
    assert track.metadata["prediction"]["turn_rate_dps"] > 0
    assert all("uncertainty_radius_m" in point for point in track.predicted_path)
    assert all("prediction_confidence" in point for point in track.predicted_path)
    assert all("model_used" in point for point in track.predicted_path)
    assert all("prediction_model" in point for point in track.predicted_path)
    assert all(point["horizon_type"] in {"short_term", "medium_term"} for point in track.predicted_path)
    assert [point["horizon_type"] for point in track.predicted_path] == [
        "short_term",
        "short_term",
        "short_term",
        "medium_term",
        "medium_term",
    ]
    assert track.predicted_path[-1]["heading"] > track.heading


def test_prediction_exposes_adaptive_model_probabilities_and_hypotheses():
    tracker = MultiTargetTracker()
    tracker.update(load_detections("frame_1.json"))
    tracks = tracker.update(load_detections("frame_2.json"))

    track = tracks[0]
    prediction_meta = track.metadata["prediction"]

    assert prediction_meta["prediction_method"] == "adaptive_multi_model_fused"
    assert set(prediction_meta["model_probabilities"]) == {
        "constant_velocity",
        "constant_acceleration",
        "coordinated_turn",
    }
    assert round(sum(prediction_meta["model_probabilities"].values()), 6) == 1.0
    assert len(prediction_meta["prediction_hypotheses"]) == 3
    assert all(len(hypothesis["points"]) == 5 for hypothesis in prediction_meta["prediction_hypotheses"])
    assert all("probability" in hypothesis for hypothesis in prediction_meta["prediction_hypotheses"])
    assert all(point["model_used"] == "adaptive_multi_model_fused" for point in track.predicted_path)
    assert all("primary_model" in point for point in track.predicted_path)


def test_prediction_eval_records_previous_forecast_error_on_update():
    tracker = MultiTargetTracker()
    tracker.update(load_detections("frame_1.json"))
    tracks = tracker.update(load_detections("frame_2.json"))

    evals = [track.metadata.get("prediction_eval") for track in tracks]

    assert all(eval_item is not None for eval_item in evals)
    assert all(eval_item["matched_horizon_s"] > 0 for eval_item in evals)
    assert all(eval_item["fde_m"] >= 0 for eval_item in evals)
    assert all(eval_item["ade_m"] >= 0 for eval_item in evals)
    assert all(eval_item["sample_count"] >= 1 for eval_item in evals)


def test_medium_update_uses_covariance_kalman_filter_metadata():
    tracker = MultiTargetTracker()
    tracker.update(load_detections("frame_1.json"), algorithm_level="medium")
    tracks = tracker.update(load_detections("frame_2.json"), algorithm_level="medium")

    kalman = tracks[0].metadata["kalman_filter"]

    assert tracks[0].metadata["filter"] == "kalman_cv"
    assert kalman["model"] == "constant_velocity_xy"
    assert len(kalman["state"]) == 4
    assert len(kalman["covariance"]) == 4
    assert len(kalman["kalman_gain"]) == 4
    assert kalman["position_sigma_m"] > 0


def _detection(
    detection_id: str,
    timestamp: float,
    lat: float,
    lon: float,
    heading: float,
    object_type: str = "aircraft",
) -> Detection:
    return Detection(
        detection_id=detection_id,
        object_type=object_type,
        timestamp=timestamp,
        lat=lat,
        lon=lon,
        alt=3000.0 if object_type != "ship" else 0.0,
        speed=100.0 if object_type != "ship" else 10.0,
        heading=heading,
        confidence=0.95,
        source_agent="association-test",
    )


def _association_identity(tracker: MultiTargetTracker, initial_ids: dict[str, str]) -> dict[str, str]:
    by_track_id = {track_id: detection_id for detection_id, track_id in initial_ids.items()}
    return {
        str(track.metadata["last_detection_id"]): by_track_id[track.track_id]
        for track in tracker.tracks.values()
        if track.metadata.get("last_detection_id") in {"next-a", "next-b"}
    }


def test_global_association_is_independent_of_detection_order():
    initial = [
        _detection("initial-a", 0.0, 31.0, 121.0, 90.0),
        _detection("initial-b", 0.0, 31.0, 121.02, 270.0),
    ]
    next_frame = [
        _detection("next-a", 10.0, 31.0, 121.0105, 90.0),
        _detection("next-b", 10.0, 31.0, 121.0095, 270.0),
    ]

    identities = []
    for ordered_frame in (next_frame, list(reversed(next_frame))):
        tracker = MultiTargetTracker()
        first = tracker.update(initial)
        initial_ids = {str(track.metadata["last_detection_id"]): track.track_id for track in first}
        tracker.update(ordered_frame)
        identities.append(_association_identity(tracker, initial_ids))

    assert identities[0] == identities[1]
    assert identities[0] == {"next-a": "initial-a", "next-b": "initial-b"}
    assert all(
        track.metadata["association"]["method"] == "global_nearest_neighbor"
        for track in tracker.tracks.values()
    )


def test_covariance_gate_rejects_statistically_implausible_measurement():
    tracker = MultiTargetTracker(association_gate_m=4_000.0)
    first = tracker.update([_detection("initial", 0.0, 31.0, 121.0, 0.0)])
    original_track_id = first[0].track_id

    tracks = tracker.update([_detection("outlier", 1.0, 31.0, 121.012, 0.0)])

    assert len(tracks) == 2
    assert tracker.tracks[original_track_id].metadata["status"] == "coasting"
    outlier_track = next(track for track in tracks if track.metadata["last_detection_id"] == "outlier")
    assert outlier_track.track_id != original_track_id


def test_known_object_types_are_not_cross_associated():
    tracker = MultiTargetTracker()
    first = tracker.update([_detection("air", 0.0, 31.0, 121.0, 90.0, "aircraft")])
    aircraft_track_id = first[0].track_id

    tracks = tracker.update([_detection("ship", 1.0, 31.0, 121.0001, 90.0, "ship")])

    assert len(tracks) == 2
    assert tracker.tracks[aircraft_track_id].object_type == "aircraft"
    assert any(track.object_type == "ship" for track in tracks)


def test_track_lifecycle_moves_tentative_to_confirmed_and_coasting():
    tracker = MultiTargetTracker(confirmation_hits=2)
    first = tracker.update([_detection("life-0", 0.0, 31.0, 121.0, 90.0)])

    assert first[0].metadata["lifecycle_state"] == "tentative"
    assert first[0].metadata["hit_count"] == 1

    second = tracker.update([_detection("life-1", 10.0, 31.0, 121.0105, 90.0)])

    assert second[0].metadata["lifecycle_state"] == "confirmed"
    assert second[0].metadata["hit_count"] == 2
    assert second[0].metadata["consecutive_hit_count"] == 2

    tracker.update([_detection("other", 20.0, 32.0, 122.0, 90.0, "uav")])

    original = next(track for track in tracker.tracks.values() if track.metadata.get("last_detection_id") == "life-1")
    assert original.metadata["lifecycle_state"] == "coasting"
    assert original.metadata["confirmed_once"] is True


def test_duplicate_frame_is_ignored_without_growing_history():
    tracker = MultiTargetTracker()
    detection = _detection("duplicate", 10.0, 31.0, 121.0, 90.0)
    first = tracker.update([detection])
    track_id = first[0].track_id

    second = tracker.update([detection])

    assert len(second) == 1
    assert len(second[0].history_path) == 1
    assert second[0].track_id == track_id
    assert tracker.diagnostics()["ignored_duplicate_detection_count"] == 1


def test_out_of_order_frame_is_ignored_without_rewinding_track_time():
    tracker = MultiTargetTracker()
    tracker.update([_detection("ordered-0", 20.0, 31.0, 121.0, 90.0)])

    tracks = tracker.update([_detection("late-0", 10.0, 31.0, 121.001, 90.0)])

    assert len(tracks) == 1
    assert tracks[0].last_update_time == 20.0
    assert len(tracks[0].history_path) == 1
    assert tracker.diagnostics()["ignored_out_of_order_detection_count"] == 1

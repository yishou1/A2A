from app.models import TrackState
from app.st_gnn_predictor import STGNNInspiredPredictor
from app.utils import project_position, speed_heading_to_velocity


def make_track(track_id, lat, lon, heading=90.0, speed=80.0):
    vx, vy = speed_heading_to_velocity(speed, heading)
    pred_lat, pred_lon = project_position(lat, lon, vx, vy, 10.0)
    return TrackState(
        track_id=track_id,
        object_type="aircraft",
        lat=lat,
        lon=lon,
        alt=3000,
        speed=speed,
        heading=heading,
        vx=vx,
        vy=vy,
        track_quality=0.9,
        last_update_time=1000.0,
        history_path=[{"timestamp": 1000.0, "lat": lat, "lon": lon, "alt": 3000, "speed": speed, "heading": heading}],
        predicted_path=[{"dt_s": 10.0, "timestamp": 1010.0, "lat": pred_lat, "lon": pred_lon, "alt": 3000}],
    )


def test_nearby_comoving_tracks_get_graph_refinement_metadata():
    tracks = [
        make_track("trk-1", 31.0, 121.0),
        make_track("trk-2", 31.001, 121.001, heading=92.0, speed=82.0),
    ]
    refined = STGNNInspiredPredictor().refine(tracks)

    assert refined[0].metadata["st_gnn_inspired"]["enabled"] is True
    assert refined[0].predicted_path[0]["st_gnn_inspired"] is True
    assert refined[0].predicted_path[0]["graph_neighbor_count"] == 1
    assert refined[0].predicted_path[0]["model_used"].endswith("_graph_refined")
    assert refined[0].predicted_path[0]["prediction_model"].endswith("_graph_refined")


def test_far_tracks_do_not_influence_each_other():
    tracks = [
        make_track("trk-1", 31.0, 121.0),
        make_track("trk-2", 32.0, 122.0),
    ]
    refined = STGNNInspiredPredictor().refine(tracks)

    assert refined[0].metadata["st_gnn_inspired"]["enabled"] is False
    assert refined[0].predicted_path[0]["graph_neighbor_count"] == 0

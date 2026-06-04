from app.models import TrackState
from app.threat_ranker import ThreatRanker
from app.utils import speed_heading_to_velocity


def make_track(track_id: str, lat: float, lon: float, object_type: str = "aircraft") -> TrackState:
    vx, vy = speed_heading_to_velocity(100, 180)
    return TrackState(
        track_id=track_id,
        object_type=object_type,
        lat=lat,
        lon=lon,
        alt=1000,
        speed=100,
        heading=180,
        vx=vx,
        vy=vy,
        track_quality=0.9,
        last_update_time=1000,
        history_path=[{"timestamp": 1000, "lat": lat, "lon": lon, "alt": 1000}],
        predicted_path=[],
        metadata={"status": "active", "anomaly": {}},
    )


def test_threat_ranking_is_sorted_by_score_descending():
    scene = {
        "protected_zone_lat": 31.2304,
        "protected_zone_lon": 121.4737,
        "protected_radius_m": 25000,
    }
    tracks = [
        make_track("far", 32.2, 122.2, "ship"),
        make_track("near", 31.2310, 121.4740, "unknown"),
        make_track("mid", 31.35, 121.55, "uav"),
    ]

    ranked = ThreatRanker().rank(tracks, scene)

    assert [item.score for item in ranked] == sorted([item.score for item in ranked], reverse=True)
    assert ranked[0].track_id == "near"
    assert ranked[0].evidence

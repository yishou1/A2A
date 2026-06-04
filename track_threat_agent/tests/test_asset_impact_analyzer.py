from app.asset_impact_analyzer import AssetImpactAnalyzer
from app.models import ProtectedAsset, ThreatAssessment, TrackState
from app.utils import project_position, risk_level, speed_heading_to_velocity


def make_track(track_id: str, lat: float, lon: float, speed: float = 80, heading: float = 90) -> TrackState:
    vx, vy = speed_heading_to_velocity(speed, heading)
    predicted_path = []
    for dt in (10.0, 20.0, 30.0):
        pred_lat, pred_lon = project_position(lat, lon, vx, vy, dt)
        predicted_path.append({"dt_s": dt, "timestamp": 1000 + dt, "lat": pred_lat, "lon": pred_lon, "alt": 1000})
    return TrackState(
        track_id=track_id,
        object_type="unknown",
        lat=lat,
        lon=lon,
        alt=1000,
        speed=speed,
        heading=heading,
        vx=vx,
        vy=vy,
        track_quality=0.8,
        last_update_time=1000,
        history_path=[{"timestamp": 1000, "lat": lat, "lon": lon, "alt": 1000}],
        predicted_path=predicted_path,
        metadata={"status": "active", "anomaly": {"heading_jump": True}},
    )


def make_threat(track_id: str, score: float) -> ThreatAssessment:
    return ThreatAssessment(
        threat_id=f"thr-{track_id}",
        track_id=track_id,
        score=score,
        level=risk_level(score),
        rank=1,
        factors={},
        evidence=[],
        timestamp=1000,
    )


def test_asset_impact_analysis_scores_protected_asset_relationships():
    asset = ProtectedAsset(
        asset_id="blue-c2-node",
        asset_name="Blue C2 Node",
        asset_type="command_post",
        lat=31.2304,
        lon=121.4737,
        protection_radius_m=9000,
        criticality=0.95,
    )
    track = make_track("trk-near", 31.2304, 121.4300, speed=120, heading=90)

    impacts = AssetImpactAnalyzer().assess([track], [make_threat(track.track_id, 0.7)], [asset])

    assert impacts
    assert impacts[0].protected_asset_id == "blue-c2-node"
    assert impacts[0].source_track_id == "trk-near"
    assert impacts[0].score > 0
    assert impacts[0].evidence

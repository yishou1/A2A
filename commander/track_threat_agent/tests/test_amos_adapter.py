from app.amos_adapter import build_amos_events, build_integration_events
from app.models import ThreatAssessment, TrackGroup, TrackState


def test_amos_adapter_outputs_required_event_shapes():
    track = TrackState(
        track_id="trk-1",
        object_type="aircraft",
        lat=31.0,
        lon=121.0,
        alt=1000,
        speed=100,
        heading=90,
        vx=100,
        vy=0,
        track_quality=0.8,
        last_update_time=10,
        history_path=[{"timestamp": 10, "lat": 31.0, "lon": 121.0, "alt": 1000}],
        predicted_path=[{"dt_s": 10, "timestamp": 20, "lat": 31.0, "lon": 121.01, "alt": 1000}],
        metadata={"source_agent": "unit"},
    )
    threat = ThreatAssessment(
        threat_id="thr-1",
        track_id="trk-1",
        score=0.7,
        level="medium",
        rank=1,
        factors={"distance_factor": 0.9},
        evidence=["unit-test"],
        timestamp=10,
    )
    group = TrackGroup(
        group_id="grp-1",
        group_type="air_formation",
        member_track_ids=["trk-1"],
        centroid={"lat": 31.0, "lon": 121.0, "alt": 1000},
        centroid_prediction=[],
        envelope={"min_lat": 31.0, "max_lat": 31.0, "min_lon": 121.0, "max_lon": 121.0},
        predicted_envelope={"min_lat": 31.0, "max_lat": 31.0, "min_lon": 121.0, "max_lon": 121.01},
        cohesion_score=0.8,
        group_threat_score=0.6,
        group_threat_level="medium",
        evidence=["group-test"],
        timestamp=10,
    )

    events = build_amos_events([track], [threat], [group], [{"rank": 1, "entity_type": "track"}])
    by_type = {event["event_type"]: event for event in events}

    asset_events = [event for event in events if event["event_type"] == "asset.updated"]
    assert len(asset_events) == 2
    assert asset_events[0]["asset_category"] == "tracked_object"
    assert asset_events[1]["asset_category"] == "track_group"
    assert by_type["asset.relationship.updated"]["target_asset_ids"] == ["trk-1"]
    assert by_type["track.updated"]["current_position"] == {"lat": 31.0, "lon": 121.0, "alt": 1000.0}
    assert by_type["threat.updated"]["threat_type"] == "aircraft"
    assert by_type["threat.updated"]["metadata"]["threat_score"] == 0.7
    assert by_type["track.group.updated"]["members"] == ["trk-1"]
    assert by_type["threat.group.updated"]["score"] == 0.6
    assert by_type["threat.ranking.updated"]["ranking"][0]["rank"] == 1


def test_integration_event_builder_keeps_generic_event_shape():
    track = TrackState(
        track_id="trk-2",
        object_type="uav",
        lat=31.0,
        lon=121.0,
        alt=500,
        speed=40,
        heading=180,
        vx=0,
        vy=-40,
        track_quality=0.8,
        last_update_time=10,
        history_path=[{"timestamp": 10, "lat": 31.0, "lon": 121.0, "alt": 500}],
        predicted_path=[],
        metadata={"source_agent": "unit"},
    )
    threat = ThreatAssessment(
        threat_id="thr-2",
        track_id="trk-2",
        score=0.5,
        level="medium",
        rank=1,
        factors={},
        evidence=["unit-test"],
        timestamp=10,
    )

    events = build_integration_events([track], [threat], [], [{"rank": 1, "entity_type": "track"}])
    event_types = {event["event_type"] for event in events}

    assert "track.updated" in event_types
    assert "threat.updated" in event_types
    assert "threat.ranking.updated" in event_types

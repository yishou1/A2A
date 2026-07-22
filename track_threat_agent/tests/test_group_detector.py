from app.group_detector import GroupDetector
from app.models import ThreatAssessment, TrackState
from app.utils import project_position, risk_level, speed_heading_to_velocity


def make_track(
    track_id: str,
    object_type: str,
    lat: float,
    lon: float,
    speed: float,
    heading: float,
) -> TrackState:
    vx, vy = speed_heading_to_velocity(speed, heading)
    predicted_path = []
    for dt in (10.0, 20.0, 30.0, 60.0, 120.0):
        pred_lat, pred_lon = project_position(lat, lon, vx, vy, dt)
        predicted_path.append(
            {
                "dt_s": dt,
                "timestamp": 1000 + dt,
                "lat": pred_lat,
                "lon": pred_lon,
                "alt": 1000 if object_type != "ship" else 0,
            }
        )
    return TrackState(
        track_id=track_id,
        object_type=object_type,
        lat=lat,
        lon=lon,
        alt=1000 if object_type != "ship" else 0,
        speed=speed,
        heading=heading,
        vx=vx,
        vy=vy,
        track_quality=0.85,
        last_update_time=1000,
        history_path=[{"timestamp": 1000, "lat": lat, "lon": lon, "alt": 1000}],
        predicted_path=predicted_path,
        metadata={"status": "active", "anomaly": {}},
    )


def make_threat(track_id: str, score: float) -> ThreatAssessment:
    return ThreatAssessment(
        threat_id=f"thr-{track_id}",
        track_id=track_id,
        score=score,
        level=risk_level(score),
        rank=1,
        factors={},
        evidence=["unit-test"],
        timestamp=1000,
    )


def test_three_nearby_aircraft_form_air_formation():
    tracks = [
        make_track("a1", "aircraft", 31.4200, 121.3000, 210, 132),
        make_track("a2", "aircraft", 31.4260, 121.3090, 208, 134),
        make_track("a3", "aircraft", 31.4140, 121.2910, 212, 131),
    ]
    groups = GroupDetector().detect(tracks, [make_threat(t.track_id, 0.5) for t in tracks])

    assert len(groups) == 1
    assert groups[0].group_type == "air_formation"
    assert set(groups[0].member_track_ids) == {"a1", "a2", "a3"}
    assert [point["dt_s"] for point in groups[0].centroid_prediction] == [10.0, 20.0, 30.0, 60.0, 120.0]


def test_two_nearby_ships_form_surface_group():
    tracks = [
        make_track("s1", "ship", 31.0200, 121.8200, 13, 292),
        make_track("s2", "ship", 31.0320, 121.8380, 12, 290),
    ]
    groups = GroupDetector().detect(tracks, [make_threat(t.track_id, 0.45) for t in tracks])

    assert len(groups) == 1
    assert groups[0].group_type == "surface_group"


def test_far_or_heading_mismatched_tracks_do_not_form_group():
    tracks = [
        make_track("a1", "aircraft", 31.4200, 121.3000, 210, 132),
        make_track("a2", "aircraft", 31.9000, 121.9000, 210, 132),
        make_track("a3", "aircraft", 31.4210, 121.3010, 210, 230),
    ]
    groups = GroupDetector().detect(tracks, [make_threat(t.track_id, 0.5) for t in tracks])

    assert groups == []


def test_group_threat_score_is_calculated():
    tracks = [
        make_track("s1", "ship", 31.0200, 121.8200, 13, 292),
        make_track("s2", "ship", 31.0320, 121.8380, 12, 290),
    ]
    threats = [make_threat("s1", 0.7), make_threat("s2", 0.55)]
    groups = GroupDetector().detect(tracks, threats)

    assert groups[0].group_threat_score > 0
    assert groups[0].group_threat_level in {"low", "medium", "high"}


def test_external_semantic_fields_do_not_change_physical_group_score():
    semantic_tracks = [
        make_track("red1", "unknown", 31.4200, 121.3000, 40, 132),
        make_track("red2", "unknown", 31.4210, 121.3010, 41, 133),
    ]
    for track in semantic_tracks:
        track.metadata.update({"affiliation": "red", "label": "hostile", "threat_level": "high"})

    plain_tracks = [
        make_track("plain1", "unknown", 31.4200, 121.3000, 40, 132),
        make_track("plain2", "unknown", 31.4210, 121.3010, 41, 133),
    ]

    detector = GroupDetector()
    semantic_group = detector.detect(semantic_tracks, [make_threat(t.track_id, 0.5) for t in semantic_tracks])[0]
    detector.reset()
    plain_group = detector.detect(plain_tracks, [make_threat(t.track_id, 0.5) for t in plain_tracks])[0]

    assert semantic_group.cohesion_score == plain_group.cohesion_score
    assert semantic_group.group_threat_score == plain_group.group_threat_score
    assert not any("semantic" in evidence.lower() or "语义" in evidence for evidence in semantic_group.evidence)


def test_chain_links_do_not_merge_distant_endpoints_into_one_group():
    tracks = [
        make_track("a", "aircraft", 31.0000, 121.0000, 100, 90),
        make_track("b", "aircraft", 31.0000, 121.0250, 100, 90),
        make_track("c", "aircraft", 31.0000, 121.0500, 100, 90),
    ]

    groups = GroupDetector(max_distance_m=3_000.0).detect(
        tracks,
        [make_threat(track.track_id, 0.5) for track in tracks],
    )

    assert len(groups) == 1
    assert len(groups[0].member_track_ids) == 2
    assert set(groups[0].member_track_ids) in ({"a", "b"}, {"b", "c"})


def test_group_prediction_aggregates_uncertainty_and_expands_envelope():
    tracks = [
        make_track("a1", "aircraft", 31.4200, 121.3000, 210, 132),
        make_track("a2", "aircraft", 31.4260, 121.3090, 208, 134),
    ]
    for index, track in enumerate(tracks):
        for point in track.predicted_path:
            point.update(
                {
                    "prediction_confidence": 0.8 + index * 0.1,
                    "uncertainty_radius_m": 400.0 + index * 200.0,
                    "model_used": "st_gnn_torchscript",
                    "model_version": "aircraft-release-v1",
                }
            )

    group = GroupDetector().detect(
        tracks,
        [make_threat(track.track_id, 0.5) for track in tracks],
    )[0]

    first_prediction = group.centroid_prediction[0]
    raw_prediction_lats = [track.predicted_path[0]["lat"] for track in tracks]
    raw_prediction_lons = [track.predicted_path[0]["lon"] for track in tracks]
    assert first_prediction["prediction_confidence"] == 0.85
    assert first_prediction["uncertainty_radius_m"] >= 500.0
    assert first_prediction["model_versions"] == ["aircraft-release-v1"]
    assert group.predicted_envelope["uncertainty_expansion_m"] == 600.0
    assert group.predicted_envelope["min_lat"] < min(raw_prediction_lats)
    assert group.predicted_envelope["max_lat"] > max(raw_prediction_lats)
    assert group.predicted_envelope["min_lon"] < min(raw_prediction_lons)
    assert group.predicted_envelope["max_lon"] > max(raw_prediction_lons)


def test_group_lifecycle_keeps_id_across_short_detection_gap():
    detector = GroupDetector(confirmation_hits=2, max_missed_frames=2)
    tracks = [
        make_track("a1", "aircraft", 31.4200, 121.3000, 210, 132),
        make_track("a2", "aircraft", 31.4260, 121.3090, 208, 134),
    ]
    threats = [make_threat(track.track_id, 0.5) for track in tracks]

    tentative = detector.detect(tracks, threats)[0]
    confirmed = detector.detect(tracks, threats)[0]
    coasting = detector.detect([tracks[0]], [threats[0]])[0]
    recovered = detector.detect(tracks, threats)[0]

    assert tentative.metadata["lifecycle_state"] == "tentative"
    assert confirmed.metadata["lifecycle_state"] == "confirmed"
    assert coasting.metadata["lifecycle_state"] == "coasting"
    assert coasting.metadata["missed_count"] == 1
    assert recovered.metadata["lifecycle_state"] == "confirmed"
    assert {tentative.group_id, confirmed.group_id, coasting.group_id, recovered.group_id} == {
        tentative.group_id
    }


def test_group_lifecycle_expires_after_coasting_limit():
    detector = GroupDetector(confirmation_hits=1, max_missed_frames=1)
    tracks = [
        make_track("s1", "ship", 31.0200, 121.8200, 13, 292),
        make_track("s2", "ship", 31.0320, 121.8380, 12, 290),
    ]
    threats = [make_threat(track.track_id, 0.5) for track in tracks]

    confirmed = detector.detect(tracks, threats)[0]
    coasting = detector.detect([], [])[0]
    expired = detector.detect([], [])

    assert confirmed.metadata["lifecycle_state"] == "confirmed"
    assert coasting.metadata["lifecycle_state"] == "coasting"
    assert expired == []


def test_group_detection_writes_physical_context_to_member_tracks():
    tracks = [
        make_track("a1", "aircraft", 31.4200, 121.3000, 210, 132),
        make_track("a2", "aircraft", 31.4260, 121.3090, 208, 134),
    ]

    group = GroupDetector(confirmation_hits=1).detect(
        tracks,
        [make_threat(track.track_id, 0.5) for track in tracks],
    )[0]

    for track in tracks:
        context = track.metadata["physical_group_context"]
        assert context["group_id"] == group.group_id
        assert context["group_type"] == "air_formation"
        assert context["cohesion_score"] == group.cohesion_score

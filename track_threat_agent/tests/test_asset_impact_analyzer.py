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


def test_semantic_relations_raise_asset_impact_score():
    asset = ProtectedAsset(
        asset_id="mission-area",
        asset_name="Mission Area",
        asset_type="civil_infrastructure",
        lat=31.2304,
        lon=121.4737,
        protection_radius_m=9000,
        criticality=0.7,
    )
    baseline = make_track("baseline", 31.2304, 121.4300, speed=60, heading=90)
    semantic = make_track("semantic", 31.2304, 121.4300, speed=60, heading=90)
    semantic.metadata.update(
        {
            "label": "hostile",
            "affiliation": "red",
            "threat_level": "high",
            "knowledge_relations": [{"predicate": "threat_of", "object": "mission_area"}],
        }
    )

    impacts = AssetImpactAnalyzer().assess(
        [baseline, semantic],
        [make_threat("baseline", 0.45), make_threat("semantic", 0.45)],
        [asset],
    )
    by_track = {impact.source_track_id: impact for impact in impacts}

    assert by_track["semantic"].score > by_track["baseline"].score
    assert by_track["semantic"].factors["semantic_asset_factor"] > 0.8
    assert any("情报" in evidence or "knowledge" in evidence.lower() for evidence in by_track["semantic"].evidence)


def test_asset_impact_reports_closest_time_priority_and_vulnerability():
    assets = [
        ProtectedAsset(
            asset_id="blue-harbor",
            asset_name="蓝方港口补给点",
            asset_type="harbor",
            lat=31.2304,
            lon=121.4737,
            protection_radius_m=12_000,
            priority=0.92,
            vulnerability=0.8,
        ),
        ProtectedAsset(
            asset_id="blue-radar",
            asset_name="蓝方岸基雷达",
            asset_type="radar_site",
            lat=31.40,
            lon=121.80,
            protection_radius_m=8_000,
            priority=0.7,
            vulnerability=0.4,
        ),
    ]
    track = make_track("trk-harbor-approach", 31.2304, 121.4300, speed=120, heading=90)

    impacts = AssetImpactAnalyzer().assess([track], [make_threat(track.track_id, 0.75)], assets)

    assert impacts[0].protected_asset_id == "blue-harbor"
    assert impacts[0].closest_time_s in {10.0, 20.0, 30.0}
    assert impacts[0].factors["asset_priority_factor"] == 0.92
    assert impacts[0].factors["asset_vulnerability_factor"] == 0.8
    assert impacts[0].metadata["asset_radius_m"] == 12_000
    assert any("最近时间" in evidence for evidence in impacts[0].evidence)


def test_asset_impact_reports_eta_and_radius_entry_status():
    asset = ProtectedAsset(
        asset_id="blue-command-post",
        asset_name="蓝方指挥所",
        asset_type="command_post",
        lat=31.2304,
        lon=121.4737,
        protection_radius_m=10_000,
        criticality=0.95,
        vulnerability=0.85,
    )
    track = make_track("trk-entering-radius", 31.2304, 121.3300, speed=180, heading=90)

    impacts = AssetImpactAnalyzer().assess([track], [make_threat(track.track_id, 0.8)], [asset])

    assert impacts
    impact = impacts[0]
    assert impact.will_enter_protection_radius is True
    assert impact.eta_to_protected_radius_s in {10.0, 20.0, 30.0}
    assert impact.predicted_min_distance_margin_m < 0
    assert impact.metadata["asset_radius_m"] == 10_000
    assert any("进入保护半径" in evidence for evidence in impact.evidence)

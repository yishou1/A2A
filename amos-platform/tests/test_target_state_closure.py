from __future__ import annotations

from types import SimpleNamespace

from amos_platform.agents.a2a.commander_projection import apply_commander_assessments
from amos_platform.fusion.track_fusion import FusedTrack
from amos_platform.simulation.director import DirectorService
from amos_platform.simulation.engine import SimEngine
from amos_platform.simulation.media_capture import MediaCaptureRuntime
from amos_platform.simulation.state_projector import _coordination_links


def _track(track_id: str, truth_id: str) -> FusedTrack:
    track = FusedTrack(track_id, 18.0, 122.0, "RADAR", domain_hint="ground", sim_time=100)
    track.associated_threat_id = truth_id
    track.classification = "MOBILE_RADAR"
    track.threat_level = "HIGH"
    track.kill_chain_phase = "TARGET"
    track.agent_assessment = {
        "status": "confirmed",
        "source": "test",
        "source_simulation_time_sec": 100,
    }
    return track


def test_commander_refresh_preserves_execution_owned_damage_fields() -> None:
    track = _track("TRK-1", "TARGET-1")
    track.agent_assessment.update({
        "damage_state": "destroyed",
        "engagement_status": "destroyed",
        "behavior_label": "已击毁，威胁解除",
        "assessment_observer": "UAV-ISR-01",
    })
    engine = SimpleNamespace(
        sensor_fusion=SimpleNamespace(tracks={track.id: track}),
        events=[],
        scenario_story={},
        clock={"run_id": "run-1"},
    )
    workflow = {
        "workflow_id": "wf-refresh",
        "status": "completed",
        "result": {"outputs": {
            "tracking_result": {"artifact": {"tracks": [{
                "track_id": track.id,
                "object_type": "mobile_radar",
                "metadata": {
                    "source_class": "mobile_radar",
                    "label": "hostile",
                    "affiliation": "red",
                    "threat_level": "high",
                },
                "lat": track.lat,
                "lon": track.lng,
            }]}},
            "threat_assessment_result": {"artifact": {"threats": [{
                "track_id": track.id, "score": 0.94, "level": "high",
            }]}},
        }},
    }

    result = apply_commander_assessments(engine, workflow)

    assert result["applied_count"] == 1
    assert track.agent_assessment["status"] == "confirmed"
    assert track.agent_assessment["damage_state"] == "destroyed"
    assert track.agent_assessment["engagement_status"] == "destroyed"
    assert track.agent_assessment["behavior_label"] == "已击毁，威胁解除"
    assert track.agent_assessment["assessment_observer"] == "UAV-ISR-01"


def test_weapon_coordination_links_are_projected_per_target() -> None:
    first = _track("TRK-1", "TARGET-1")
    second = _track("TRK-2", "TARGET-2")
    engine = SimpleNamespace(
        clock={"elapsed_sec": 500},
        assets={"A-1": {"position": {"lat": 18.5, "lng": 122.5}}},
        weapons={"W-1": {"target_threat_id": "TARGET-1"}},
        sensor_fusion=SimpleNamespace(tracks={first.id: first, second.id: second}),
        events=[
            {"type": "authorized_fire_command", "target_track_id": first.id},
            {"type": "weapon_hit", "target_threat_id": "TARGET-1"},
            {"type": "damage_assessment_confirmed", "weapon_id": "W-1",
             "target_track_id": first.id, "target_threat_id": "TARGET-1"},
        ],
        _scenario_coordination_links=[
            {"link_id": "L-1", "link_type": "weapon", "source_asset_id": "A-1",
             "target_ref": "TARGET-1"},
            {"link_id": "L-2", "link_type": "weapon", "source_asset_id": "A-1",
             "target_ref": "TARGET-2"},
        ],
        _truth_target_for_track=lambda track: track.associated_threat_id,
    )
    projected_tracks = {
        first.id: {"id": first.id, "lat": first.lat, "lng": first.lng},
        second.id: {"id": second.id, "lat": second.lat, "lng": second.lng},
    }

    links = _coordination_links(engine, projected_tracks, {"link_records": []})

    assert {link["link_id"]: link["status"] for link in links} == {
        "L-1": "complete",
        "L-2": "ready",
    }


def test_damage_assessment_gate_requires_the_declared_prior_target() -> None:
    engine = SimEngine(seed=1)
    track = _track("TRK-2", "TARGET-2")
    prior_track = _track("TRK-1", "TARGET-1")
    engine.clock["elapsed_sec"] = 500
    engine.sensor_fusion.tracks = {track.id: track, prior_track.id: prior_track}
    engine.threats = {
        "TARGET-1": {"id": "TARGET-1", "lat": 18.1, "lng": 122.1},
        "TARGET-2": {"id": "TARGET-2", "lat": 18.0, "lng": 122.0},
    }
    engine.assets = {"A-2": {"health": {"comms_strength": 95}}}
    engine._engagement_policy = {
        "authorized_asset_ids": ["A-2"],
        "authorized_weapons": ["WPN"],
        "target_engagements": {"TARGET-2": {
            "asset_id": "A-2",
            "weapon_name": "WPN",
            "requires_completed_target_ids": ["TARGET-1"],
            "requires_damage_assessment": True,
        }},
    }
    engine.events = [
        {"type": "authorized_fire_command", "target_track_id": "TRK-1"},
        {"type": "damage_assessment_confirmed", "target_threat_id": "OTHER"},
    ]

    denied = engine.engagement_eligibility(track.id, asset_id="A-2", weapon_name="WPN")
    assert denied["eligible"] is False
    assert "毁伤评估" in denied["reason"]

    engine.events.append({
        "type": "damage_assessment_confirmed", "target_threat_id": "TARGET-1",
    })
    allowed = engine.engagement_eligibility(track.id, asset_id="A-2", weapon_name="WPN")
    assert allowed["eligible"] is True


def test_optional_freshness_assessment_age_and_comms_gates_fail_closed() -> None:
    engine = SimEngine(seed=1)
    track = _track("TRK-1", "TARGET-1")
    engine.clock["elapsed_sec"] = 200
    engine.sensor_fusion.tracks = {track.id: track}
    engine.threats = {"TARGET-1": {"id": "TARGET-1", "lat": 18.0, "lng": 122.0}}
    engine.assets = {"A-1": {"health": {"comms_strength": 90}}}
    engine._engagement_policy = {
        "authorized_asset_ids": ["A-1"],
        "authorized_weapons": ["WPN"],
        "maximum_track_age_sec": 60,
        "maximum_assessment_age_sec": 60,
        "minimum_comms_strength": 80,
    }

    result = engine.engagement_eligibility(track.id, asset_id="A-1", weapon_name="WPN")
    assert result["eligible"] is False
    assert "航迹已过期" in result["reason"]

    track.last_sim_time = 190
    result = engine.engagement_eligibility(track.id, asset_id="A-1", weapon_name="WPN")
    assert result["eligible"] is False
    assert "识别评估已过期" in result["reason"]

    track.agent_assessment["source_simulation_time_sec"] = 190
    engine.assets["A-1"]["health"]["comms_strength"] = 70
    result = engine.engagement_eligibility(track.id, asset_id="A-1", weapon_name="WPN")
    assert result["eligible"] is False
    assert "通信链路质量不足" in result["reason"]

    engine.assets["A-1"]["health"]["comms_strength"] = 90
    assert engine.engagement_eligibility(
        track.id, asset_id="A-1", weapon_name="WPN",
    )["eligible"] is True


def test_target_specific_post_bda_routes_only_start_for_assessed_target() -> None:
    engine = SimEngine(seed=1)
    track = _track("TRK-1", "TARGET-1")
    engine.clock["elapsed_sec"] = 300
    engine.scenario_story = {"demo_controls": {"duration_sec": 1000}}
    engine.sensor_fusion.tracks = {track.id: track}
    engine.sensor_fusion.last_observation_batch = {"observations": []}
    engine.threats = {
        "TARGET-1": {
            "id": "TARGET-1", "lat": 18.0, "lng": 122.0,
            "neutralized": False, "damage_state": "impact_pending",
        },
    }
    engine.assets = {
        asset_id: {
            "id": asset_id,
            "position": {"lat": 18.5, "lng": 122.5, "alt_ft": 10000},
            "status": "active", "speed_kts": 100, "_cruise_speed_kts": 100,
        }
        for asset_id in ("LEGACY", "FIRST", "SECOND")
    }
    engine.weapons = {"W-1": {
        "id": "W-1", "status": "hit", "damage_state": "impact_pending",
        "impact_sim_time": 100, "target_threat_id": "TARGET-1",
        "target_track_id": track.id, "predicted_damage_state": "destroyed",
    }}
    engine._engagement_policy = {
        "post_bda_routes": {"LEGACY": [{"lat": 19.0, "lng": 123.0}]},
        "post_bda_routes_by_target": {
            "TARGET-1": {"FIRST": [{"lat": 18.7, "lng": 122.7, "label": "RTB-1"}]},
            "TARGET-2": {"SECOND": [{"lat": 18.8, "lng": 122.8, "label": "RTB-2"}]},
        },
        "post_bda_behaviors_by_target": {
            "TARGET-1": {"FIRST": {"behavior": "target_1_rtb", "speed_kts": 120}},
        },
    }
    engine._post_impact_visual_observer = lambda truth_id: "ISR-1"

    engine._confirm_pending_damage_assessments()

    assert engine.waypoint_nav.get_route("FIRST")[-1]["label"] == "RTB-1"
    assert engine.waypoint_nav.get_route("LEGACY") == []
    assert engine.waypoint_nav.get_route("SECOND") == []
    assert engine.assets["FIRST"]["_current_behavior"] == "target_1_rtb"
    assessment_event = next(
        event for event in engine.events
        if event.get("type") == "damage_assessment_confirmed"
    )
    assert assessment_event["target_track_id"] == "TRK-1"
    assert assessment_event["target_threat_id"] == "TARGET-1"
    assert assessment_event["target_ref"] == "TARGET-1"
    return_event = next(
        event for event in engine.events if event.get("type") == "post_bda_return_started"
    )
    assert return_event["asset_ids"] == ["FIRST"]
    assert return_event["target_threat_id"] == "TARGET-1"


def test_media_and_director_target_level_closure_conditions() -> None:
    media = MediaCaptureRuntime()
    media.reset(
        [{
            "capture_id": "CAP-1", "media_id": "MEDIA-1", "product_type": "external",
            "at_sec": 0,
            "capture_parameters": {
                "required_damage_assessment_target_ids": ["TARGET-1", "TARGET-2"],
            },
        }],
        [{"media_id": "MEDIA-1", "uri": "/static/media/test.png", "mime_type": "image/png"}],
    )
    engine = SimpleNamespace(
        clock={"elapsed_sec": 10, "scenario_branch": "standard", "run_id": "run-1"},
        sensor_fusion=SimpleNamespace(
            last_observation_batch={"tick_id": 1, "observations": []},
            tracks={}, truth_associations=[],
        ),
        assets={}, threats={}, tasks=[], scenario_story={},
        events=[{"type": "damage_assessment_confirmed", "target_threat_id": "TARGET-1"}],
    )
    assert media.evaluate(engine) == []
    engine.events.append({
        "type": "damage_assessment_confirmed", "target_threat_id": "TARGET-2",
    })
    assert [item["media_id"] for item in media.evaluate(engine)] == ["MEDIA-1"]

    director_engine = SimpleNamespace(
        clock={"elapsed_sec": 10},
        sensor_fusion=SimpleNamespace(get_tracks=lambda: {}),
        media_capture=SimpleNamespace(captured_media_ids=set()),
        _story_emitted=set(),
        events=[
            {"type": "damage_assessment_confirmed", "target_threat_id": "TARGET-1"},
            {"type": "asset_recovered", "asset_id": "UAV-1"},
        ],
    )
    director = DirectorService(SimpleNamespace(get_engine=lambda: director_engine))
    checkpoint = {"conditions": {
        "damage_assessment_target_ids": ["TARGET-1"],
        "recovered_asset_ids": ["UAV-1"],
    }}
    assert director._checkpoint_satisfied(checkpoint) is True
    checkpoint["conditions"]["recovered_asset_ids"].append("UAV-2")
    assert director._checkpoint_satisfied(checkpoint) is False

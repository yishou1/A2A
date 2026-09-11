from __future__ import annotations

import hashlib
from pathlib import Path

from amos_platform.agents.a2a.commander_projection import apply_commander_assessments
from amos_platform.data.scenario_repository import get_scenario
from amos_platform.domain.policies.visibility import find_truth_leaks
from amos_platform.simulation.engine import SimEngine


SCENARIO_ID = "air-space-sea-carrier-strike"
ROOT = Path(__file__).resolve().parents[1]
CLASSIFICATIONS = {
    "COASTAL-AIRFIELD-01": "airfield_runway",
    "MOBILE-COASTAL-AD-01": "mobile_coastal_air_defense",
    "CIVILIAN-PORT-01": "civilian_port",
}


def _advance(engine: SimEngine, elapsed_sec: float) -> None:
    while float(engine.clock["elapsed_sec"]) < elapsed_sec:
        engine._tick(min(30, elapsed_sec - float(engine.clock["elapsed_sec"])))


def _apply_airfield_assessments(engine: SimEngine) -> None:
    tracking_rows = []
    threat_rows = []
    for track in engine.sensor_fusion.tracks.values():
        truth_id = engine._truth_target_for_track(track)
        assert truth_id in CLASSIFICATIONS
        tracking_rows.append({
            "track_id": track.id,
            "object_type": "ground_installation",
            "metadata": {
                "source_class": CLASSIFICATIONS[truth_id],
                "label": "civilian" if truth_id == "CIVILIAN-PORT-01" else "hostile",
                "affiliation": "neutral" if truth_id == "CIVILIAN-PORT-01" else "red",
                "threat_level": "low" if truth_id == "CIVILIAN-PORT-01" else "high",
            },
            "lat": track.lat,
            "lon": track.lng,
        })
        threat_rows.append({"track_id": track.id, "score": 0.1 if truth_id == "CIVILIAN-PORT-01" else 0.94, "level": "low" if truth_id == "CIVILIAN-PORT-01" else "high"})
    projection = apply_commander_assessments(
        engine,
        {
            "workflow_id": "wf-asc-test",
            "status": "completed",
            "result": {
                "outputs": {
                    "tracking_result": [{"value": {"tracks": tracking_rows}}],
                    "threat_assessment_result": [{"value": {"threats": threat_rows}}],
                },
                "summary": {"verification": "airfield backend fixture"},
            },
        },
        submission={
            "run_id": str(engine.clock.get("run_id") or "run-asc-test"),
            "transport": "gateway",
            "package": {"package_id": "pkg-asc", "verified": True},
            "snapshot_sequence": int(
                engine.sensor_fusion.last_observation_batch.get("tick_id", 0) or 0
            ),
            "simulation_time_sec": float(engine.clock["elapsed_sec"]),
        },
    )
    assert projection["status"] == "completed"
    assert projection["applied_count"] == 3


def _track_by_truth(engine: SimEngine, truth_id: str) -> dict:
    state = engine.get_operator_state()
    return next(
        track for track in state["fused_tracks"]
        if engine._truth_target_for_track(
            engine.sensor_fusion.tracks[str(track.get("id") or track.get("track_id"))]
        ) == truth_id
    )


def test_carrier_scenario_uses_individual_resources_and_complete_offline_media() -> None:
    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    asset_ids = {item["asset_id"] for item in scenario["assets"]}
    assert asset_ids == {
        "SAT-A2S-01", "SAT-A2S-02", "SAT-COM-A2S-01", "CV-01", "DDG-01", "FFG-01", "AEW-01", "UAV-TANKER-01", "UAV-ISR-01",
        "UAV-STRIKE-01", "LOITER-UAV-01",
    }
    assert {item["threat_id"] for item in scenario["threats"]} == set(CLASSIFICATIONS)
    assert all(int(item.get("member_count", 1) or 1) == 1 for item in scenario["asset_profiles"])
    assert len(scenario["media_cues"]) == 10
    assert scenario["map_display"]["space_visual_speed_factor"] == 0.035
    assert "SAT-A2S-01" not in scenario["map_display"]["trail_asset_ids"]
    assert len(scenario["map_display"]["space_ground_tracks"]) == 2
    assert scenario["map_display"]["space_node_asset_ids"] == ["SAT-COM-A2S-01"]
    assert len(scenario["space_operations"]["passes"]) == 2
    assert scenario["concept_maturity"] == "public-capability-inspired_near-future_exercise"
    assert scenario["asset_motion_windows"]["LOITER-UAV-01"]["launch_from_asset"] == "UAV-STRIKE-01"
    roles = {item["asset_id"]: item["role"] for item in scenario["assets"]}
    assert "有人预警指挥机" in roles["AEW-01"]
    assert "无人加油" in roles["UAV-TANKER-01"]
    assert len(scenario["flight_deck_cycle"]) == 8
    assert {item["mime_type"] for item in scenario["media_cues"]} == {
        "image/png", "image/svg+xml",
    }
    for item in scenario["media_cues"]:
        path = ROOT / item["uri"].removeprefix("/")
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["checksum"]

    assignments = scenario["engagement_policy"]["target_engagements"]
    assert set(assignments) == {
        "COASTAL-AIRFIELD-01", "MOBILE-COASTAL-AD-01",
    }
    assert len({item["asset_id"] for item in assignments.values()}) == 2
    assert {item["wave"] for item in assignments.values()} == {1, 2}
    assert all(
        item.get("expend_source_asset") is True
        for item in assignments.values() if item["wave"] == 2
    )


def test_carrier_scenario_has_clear_east_to_west_sea_land_geometry() -> None:
    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    assert "北吕宋东岸" in scenario["theater"]["name"]

    target_longitudes = [item["position"]["lng"] for item in scenario["threats"]]
    carrier_route = scenario["asset_routes"]["CV-01"]
    carrier_longitudes = [item["lng"] for item in carrier_route]
    assert max(target_longitudes) <= 121.97
    assert min(carrier_longitudes) >= 122.68
    assert min(carrier_longitudes) - max(target_longitudes) >= 0.65

    initial = {item["asset_id"]: item["position"] for item in scenario["assets"]}
    assert initial["DDG-01"]["lat"] - initial["CV-01"]["lat"] >= 0.20
    assert initial["CV-01"]["lat"] - initial["FFG-01"]["lat"] >= 0.20


def test_carrier_sensor_evidence_classifies_military_and_protected_ground_contacts() -> None:
    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)
    _advance(engine, 2220)

    classifications_by_truth: dict[str, set[str]] = {}
    for track in engine.sensor_fusion.tracks.values():
        truth_id = engine._truth_target_for_track(track)
        classifications_by_truth.setdefault(str(truth_id), set()).add(track.classification)

    assert classifications_by_truth == {
        "COASTAL-AIRFIELD-01": {"AIRFIELD_RUNWAY"},
        "MOBILE-COASTAL-AD-01": {"MOBILE_COASTAL_AIR_DEFENSE"},
        "CIVILIAN-PORT-01": {"CIVILIAN_PORT"},
    }


def test_carrier_launch_visibility_and_all_air_assets_keep_moving() -> None:
    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)

    assert {item["id"] for item in engine.get_operator_state()["assets"]} == {
        "SAT-COM-A2S-01", "CV-01", "DDG-01", "FFG-01",
    }
    _advance(engine, 240)
    assert "SAT-A2S-01" in {
        item["id"] for item in engine.get_operator_state()["assets"]
    }
    _advance(engine, 600)
    assert "SAT-A2S-01" not in {
        item["id"] for item in engine.get_operator_state()["assets"]
    }
    _advance(engine, 840)
    assert {"AEW-01", "UAV-TANKER-01"}.issubset(
        {item["id"] for item in engine.get_operator_state()["assets"]}
    )
    assert "UAV-ISR-01" not in {
        item["id"] for item in engine.get_operator_state()["assets"]
    }
    _advance(engine, 960)
    assert "UAV-ISR-01" in {
        item["id"] for item in engine.get_operator_state()["assets"]
    }
    _advance(engine, 1680)
    assert "SAT-A2S-02" in {
        item["id"] for item in engine.get_operator_state()["assets"]
    }
    _advance(engine, 2040)
    assert "SAT-A2S-02" not in {
        item["id"] for item in engine.get_operator_state()["assets"]
    }
    _advance(engine, 2700)
    visible = {item["id"] for item in engine.get_operator_state()["assets"]}
    assert len(visible) == 8
    assert "LOITER-UAV-01" not in visible
    assert all(
        engine.assets[asset_id]["status"] == "active"
        and float(engine.assets[asset_id]["speed_kts"]) > 0
        and engine.waypoint_nav.get_route(asset_id)
        for asset_id in visible
        if asset_id != "SAT-COM-A2S-01"
    )
    _advance(engine, 3600)
    assert "LOITER-UAV-01" in {
        item["id"] for item in engine.get_operator_state()["assets"]
    }


def test_target_specific_weapons_wave_gate_and_one_way_asset_conversion() -> None:
    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)
    engine.clock["run_id"] = "run-asc-test"
    _advance(engine, 3400)
    _apply_airfield_assessments(engine)

    assignments = scenario["engagement_policy"]["target_engagements"]
    for truth_id, expected in assignments.items():
        track = _track_by_truth(engine, truth_id)
        assert track["engagement_action"]["asset_id"] == expected["asset_id"]
        assert track["engagement_action"]["weapon_name"] == expected["weapon_name"]
        assert track["engagement_action"]["wave"] == expected["wave"]
        # Each wave deliberately uses one named aircraft.  It is sequenced by
        # the command chain, not misrepresented as a multi-platform salvo.
        assert track["engagement_action"]["coordinated"] is False
        assert track["engagement_eligible"] is (expected["wave"] == 1)

    civilian = _track_by_truth(engine, "CIVILIAN-PORT-01")
    assert civilian["engagement_eligible"] is False

    track = _track_by_truth(engine, "COASTAL-AIRFIELD-01")
    action = track["engagement_action"]
    result = engine.fire_weapon_at_track(
        track["id"], asset_id=action["asset_id"],
        weapon_name=action["weapon_name"], authorized=True,
    )
    assert result["status"] == "launched"
    assert result["coordinated"] is False
    assert len(result["participants"]) == 1
    assert {item["asset_id"] for item in result["participants"]} == {"UAV-STRIKE-01"}
    assert find_truth_leaks(result) == []

    _advance(engine, 4390)
    assert any(
        event.get("type") == "damage_assessment_confirmed"
        for event in engine.events
    )
    mobile_track = _track_by_truth(engine, "MOBILE-COASTAL-AD-01")
    assert mobile_track["engagement_eligible"] is True
    action = mobile_track["engagement_action"]
    result = engine.fire_weapon_at_track(
        mobile_track["id"], asset_id=action["asset_id"],
        weapon_name=action["weapon_name"], authorized=True,
    )
    assert result["status"] == "launched"
    assert result["coordinated"] is False
    assert len(result["participants"]) == 1
    visible_assets = {item["id"] for item in engine.get_operator_state()["assets"]}
    asset_id = "LOITER-UAV-01"
    assert engine.assets[asset_id]["status"] == "expended"
    assert engine.assets[asset_id]["_ammo"]["巡飞攻击载荷"] == 0
    assert asset_id not in visible_assets
    assert any(
        event.get("type") == "one_way_asset_committed"
        and event.get("asset_id") == asset_id
        for event in engine.events
    )

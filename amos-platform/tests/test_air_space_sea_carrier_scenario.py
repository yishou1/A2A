from __future__ import annotations

import hashlib
import math
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
DEFAULT_TURN_RATE_DPS = {"air": 4.0, "maritime": 0.8, "space": 0.2}


def _advance(engine: SimEngine, elapsed_sec: float) -> None:
    while float(engine.clock["elapsed_sec"]) < elapsed_sec:
        engine._tick(min(30, elapsed_sec - float(engine.clock["elapsed_sec"])))


def _nm(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    return math.hypot(
        (lat1 - lat2) * 60.0,
        (lng1 - lng2) * 60.0 * math.cos(math.radians(lat1)),
    )


def _position(engine: SimEngine, asset_id: str) -> tuple[float, float]:
    pos = engine.assets[asset_id].get("position") or {}
    return float(pos.get("lat", 0) or 0), float(pos.get("lng", 0) or 0)


def _watch_motion(
    engine: SimEngine, asset_ids: list[str], until_sec: float,
) -> dict[str, list[tuple[float, float]]]:
    """Tick to ``until_sec`` and reject motion no platform could perform.

    ``WaypointNav.set_route`` replaces the entire route and resets the pending
    index, so a second writer for a route a platform is already flying shows up
    here as a teleport or a heading reversal rather than as a quiet no-op.

    Returns:
        Every sampled position per asset, for distance assertions on the path
        actually flown rather than only on the final position.
    """
    samples: dict[str, list[tuple[float, float]]] = {a: [] for a in asset_ids}
    previous: dict[str, dict[str, float]] = {}
    while float(engine.clock["elapsed_sec"]) < until_sec:
        step = min(30.0, until_sec - float(engine.clock["elapsed_sec"]))
        engine._tick(step)
        for asset_id in asset_ids:
            asset = engine.assets[asset_id]
            lat, lng = _position(engine, asset_id)
            heading = float(asset.get("heading_deg", 0) or 0)
            speed = float(asset.get("speed_kts", 0) or 0)
            samples[asset_id].append((lat, lng))
            old = previous.get(asset_id)
            if old is not None:
                moved = _nm(lat, lng, old["lat"], old["lng"])
                reach = max(speed, old["speed"]) * step / 3600.0
                assert moved <= reach * 1.6 + 0.05, (
                    f"{asset_id} moved {moved:.2f}nm in {step:.0f}s at t="
                    f"{engine.clock['elapsed_sec']:.0f} (reach {reach:.2f}nm)"
                )
                if speed > 5.0 and old["speed"] > 5.0:
                    rate = float(asset.get(
                        "max_turn_rate_dps",
                        DEFAULT_TURN_RATE_DPS.get(str(asset.get("domain")), 4.0),
                    ))
                    turned = abs((heading - old["heading"] + 180.0) % 360.0 - 180.0)
                    assert turned <= rate * step + 6.0, (
                        f"{asset_id} turned {turned:.1f}deg in {step:.0f}s at t="
                        f"{engine.clock['elapsed_sec']:.0f} (limit {rate * step:.1f})"
                    )
            previous[asset_id] = {
                "lat": lat, "lng": lng, "heading": heading, "speed": speed,
            }
    return samples


def _authorize_wave_one(engine: SimEngine) -> None:
    track = _track_by_truth(engine, "COASTAL-AIRFIELD-01")
    action = track["engagement_action"]
    result = engine.fire_weapon_at_track(
        track["id"], asset_id=action["asset_id"],
        weapon_name=action["weapon_name"], authorized=True,
    )
    assert result["status"] == "launched"


def _visible_from(scenario: dict, asset_id: str) -> float:
    """When the operator first sees an asset.

    Its placement at that instant is a launch-origin question, not a flight
    path, so the motion invariant is only meaningful from here on.
    """
    window = scenario["asset_visibility_windows"][asset_id]
    return float(window.get("visible_from_sec", 0) or 0)


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
    assert len(scenario["media_cues"]) == 9
    assert scenario["map_display"]["space_visual_speed_factor"] == 0.035
    assert scenario["map_display"]["default_layers"]["coordination"] is True
    assert scenario["map_display"]["coordination_link_types"] == ["weapon"]
    assert scenario["map_display"]["trail_window_sec"] == 240
    assert scenario["map_display"]["track_trail_window_sec"] == 360
    assert "SAT-A2S-01" not in scenario["map_display"]["trail_asset_ids"]
    assert {"SAT-A2S-01", "SAT-A2S-02"}.issubset(
        scenario["map_display"]["label_asset_ids"]
    )
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
        assert "authorization" not in path.name.lower()
        if path.suffix == ".svg":
            svg = path.read_text(encoding="utf-8")
            assert "授权执行（操作员）" not in svg
            assert "拒绝 / 返回重规划" not in svg

    assert all(
        (item.get("capture_parameters") or {}).get("renderer_type")
        != "authorization_state"
        for item in scenario["capture_plans"]
    )

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
    checkpoints = {
        item["checkpoint_id"]: item for item in scenario["demo_checkpoints"]
    }
    assert checkpoints["ASC-CP-WAVE2"]["min_elapsed_sec"] == 4380
    assert checkpoints["ASC-CP-WAVE2"]["conditions"]["event_types_emitted"] == [
        "damage_assessment_confirmed"
    ]
    assert checkpoints["ASC-CP-WAVE1"]["engagement_wave"] == 1
    assert checkpoints["ASC-CP-WAVE1"]["conditions"]["media_ids_released"] == [
        "ASC-MEDIA-06"
    ]
    assert checkpoints["ASC-CP-WAVE2"]["engagement_wave"] == 2
    assert checkpoints["ASC-CP-CLOSE"]["operator_action_type"] == "review"


def test_satellites_traverse_the_complete_local_ground_track_during_access() -> None:
    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)
    initial = _position(engine, "SAT-A2S-01")

    _advance(engine, 240)
    assert _nm(*initial, *_position(engine, "SAT-A2S-01")) < 0.01

    _advance(engine, 360)
    capture_position = _position(engine, "SAT-A2S-01")
    target = next(
        item["position"] for item in scenario["threats"]
        if item["threat_id"] == "COASTAL-AIRFIELD-01"
    )
    assert _nm(*capture_position, target["lat"], target["lng"]) < 35

    _advance(engine, 599)
    final_point = scenario["asset_routes"]["SAT-A2S-01"][-1]
    assert _nm(*_position(engine, "SAT-A2S-01"), final_point["lat"], final_point["lng"]) < 0.5


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


def test_withheld_reauthorization_keeps_the_one_way_payload_off_target() -> None:
    """The payload's terminal run is an authorization outcome, not a timer.

    A time-only terminal-attack phase fires whether or not the operator ever
    releases the payload, which walks a still-armed one-way munition up to the
    target with no authorization behind it.  This test flies the whole timeline
    out with wave 2 withheld and requires the payload to stay in its loiter
    area, armed, for the entire window the operator can see it.
    """
    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)
    engine.clock["run_id"] = "run-asc-test"

    _advance(engine, 3400)
    _apply_airfield_assessments(engine)
    _authorize_wave_one(engine)

    # Wave 2 is never authorized.  Fly out the rest of the demonstrated
    # timeline, rejecting motion no platform could perform along the way.
    _advance(engine, _visible_from(scenario, "LOITER-UAV-01"))
    samples = _watch_motion(engine, ["LOITER-UAV-01", "UAV-STRIKE-01"], 5400)
    flown = samples["LOITER-UAV-01"]
    assert flown

    payload = engine.assets["LOITER-UAV-01"]
    assert payload["_current_behavior"] == "conditional_attack_hold"
    assert payload["status"] == "active"
    assert payload["_ammo"]["巡飞攻击载荷"] == 1
    assert not [
        event for event in engine.events
        if event.get("type") == "one_way_asset_committed"
    ]
    # The withheld shot releases nothing at all.
    assert not [
        weapon for weapon in engine.weapons.values()
        if weapon.get("source_asset_id") == "LOITER-UAV-01"
    ]

    threat = engine.threats["MOBILE-COASTAL-AD-01"]
    port = engine.threats["CIVILIAN-PORT-01"]
    closest_target = min(
        _nm(lat, lng, float(threat["lat"]), float(threat["lng"]))
        for lat, lng in flown
    )
    closest_port = min(
        _nm(lat, lng, float(port["lat"]), float(port["lng"]))
        for lat, lng in flown
    )
    # It holds off the mobile launcher rather than closing on it...
    assert closest_target >= 5.0, closest_target
    # ...and never enters the protected zone it must never be fired into.
    assert closest_port > float(
        scenario["engagement_policy"]["protected_asset_buffer_nm"]
    ), closest_port


def test_release_hold_route_is_written_only_by_the_fire_command() -> None:
    """One writer per route: the fire command, not a timed phase.

    ``WaypointNav.set_route`` replaces the whole route and resets the pending
    index, so a timed phase that re-sets the same geometry the release command
    already set would yank the aircraft back to the first waypoint mid-flight.
    """
    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    standoff = next(
        phase for phase in scenario["asset_behavior_phases"]["UAV-STRIKE-01"]
        if phase["behavior"] == "standoff_weapon_hold"
    )
    standoff_labels = {point["label"] for point in standoff["route"]}
    held_labels = {
        point["label"]
        for point in scenario["engagement_policy"]["post_launch_routes"]["UAV-STRIKE-01"]
    }
    # The two routes must be tellable apart, or this test proves nothing.
    assert not (standoff_labels & held_labels)

    def fly(fire: bool) -> tuple[SimEngine, set[str], str]:
        engine = SimEngine(seed=int(scenario["default_seed"]))
        engine.load_scenario(scenario)
        engine.clock["run_id"] = "run-asc-test"
        _advance(engine, _visible_from(scenario, "UAV-STRIKE-01"))
        _watch_motion(engine, ["UAV-STRIKE-01"], 3400)
        _apply_airfield_assessments(engine)
        if fire:
            _authorize_wave_one(engine)
        _watch_motion(engine, ["UAV-STRIKE-01"], 4800)
        asset = engine.assets["UAV-STRIKE-01"]
        return engine, {
            point["label"] for point in engine.waypoint_nav.get_route("UAV-STRIKE-01")
        }, asset["_current_behavior"]

    withheld, labels, behavior = fly(fire=False)
    # With no release, nothing rewrites the standoff route on a schedule.
    assert labels <= standoff_labels, labels
    assert behavior == "standoff_weapon_hold"
    assert withheld.assets["UAV-STRIKE-01"]["_ammo"]["舰载无人机空地导弹"] == 1

    released, labels, behavior = fly(fire=True)
    assert labels <= held_labels, labels
    assert behavior == "payload_release_and_recovery_hold"
    assert released.assets["UAV-STRIKE-01"]["_ammo"]["舰载无人机空地导弹"] == 0


def test_delayed_reauthorization_finds_the_payload_where_the_loiter_left_it() -> None:
    """A late decision must still be able to release the payload.

    Nothing on a timer moves a one-way munition while the operator deliberates,
    so a delayed re-authorization finds it still circling its loiter area and
    the strike proceeds from wherever the orbit happens to be — rather than
    from a precomputed attack point that assumed an earlier decision.
    """
    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)
    engine.clock["run_id"] = "run-asc-test"

    _advance(engine, 3400)
    _apply_airfield_assessments(engine)
    _authorize_wave_one(engine)

    # Well past the nominal 4390 s decision point, and past the 4200 s phase.
    _advance(engine, 5000)

    payload = engine.assets["LOITER-UAV-01"]
    assert payload["_current_behavior"] == "conditional_attack_hold"
    assert payload["status"] == "active"
    assert payload["_ammo"]["巡飞攻击载荷"] == 1
    lat, lng = _position(engine, "LOITER-UAV-01")
    threat = engine.threats["MOBILE-COASTAL-AD-01"]
    assert _nm(lat, lng, float(threat["lat"]), float(threat["lng"])) >= 5.0

    track = _track_by_truth(engine, "MOBILE-COASTAL-AD-01")
    assert track["engagement_eligible"] is True
    action = track["engagement_action"]
    result = engine.fire_weapon_at_track(
        track["id"], asset_id=action["asset_id"],
        weapon_name=action["weapon_name"], authorized=True,
    )
    assert result["status"] == "launched"

    released = [
        weapon for weapon in engine.weapons.values()
        if weapon.get("source_asset_id") == "LOITER-UAV-01"
    ]
    assert len(released) == 1
    assert released[0]["launch_time"] >= 5000
    assert engine.assets["LOITER-UAV-01"]["_ammo"]["巡飞攻击载荷"] == 0
    assert engine.assets["LOITER-UAV-01"]["_current_behavior"] == "terminal_attack_committed"
    assert any(
        event.get("type") == "one_way_asset_committed"
        and event.get("asset_id") == "LOITER-UAV-01"
        for event in engine.events
    )

    _advance(engine, 5600)
    assert any(
        event.get("type") == "weapon_hit" and event.get("weapon_id") == released[0]["id"]
        for event in engine.events
    )


def test_wave_two_state_change_preserves_the_live_loiter_route() -> None:
    """The decision phase updates status without restarting the patrol geometry."""
    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)

    _advance(engine, 4199)
    before = engine.waypoint_nav.get_route("LOITER-UAV-01")
    assert before
    _advance(engine, 4201)

    assert engine.assets["LOITER-UAV-01"]["_current_behavior"] == "conditional_attack_hold"
    assert engine.waypoint_nav.get_route("LOITER-UAV-01") == before


def test_first_wave_bda_keeps_isr_on_station_until_the_recovery_window() -> None:
    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)
    engine.clock["run_id"] = "run-asc-test"

    _advance(engine, 3899.999)
    _apply_airfield_assessments(engine)
    _authorize_wave_one(engine)
    _advance(engine, 4380)

    assert any(
        event.get("type") == "damage_assessment_confirmed"
        for event in engine.events
    )
    assert engine.assets["UAV-ISR-01"]["_current_behavior"] == "post_strike_bda_orbit"
    _advance(engine, 5040)
    assert engine.assets["UAV-ISR-01"]["_current_behavior"] == "carrier_recovery"


def test_scenario_stays_consistent_when_no_backend_assessment_ever_arrives() -> None:
    """No classification, no strike — and no asset placed on a stale assumption.

    Nothing in the choreography may be calibrated to a backend response that
    might be late or missing: with the assessment fixture never applied, every
    platform must simply fly its declared timeline and the armed payload must
    end the run still in its loiter area.
    """
    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)
    engine.clock["run_id"] = "run-asc-test"
    duration = float(scenario["demo_controls"]["duration_sec"])

    _advance(engine, _visible_from(scenario, "LOITER-UAV-01"))
    samples = _watch_motion(engine, ["LOITER-UAV-01", "UAV-STRIKE-01"], duration)

    assert engine.weapons == {}
    assert not [
        event for event in engine.events
        if event.get("type") in {"one_way_asset_committed", "weapon_hit"}
    ]

    payload = engine.assets["LOITER-UAV-01"]
    assert payload["_current_behavior"] == "conditional_attack_hold"
    assert payload["status"] == "active"
    assert payload["_ammo"]["巡飞攻击载荷"] == 1
    # UAV-STRIKE-01 still recovers on schedule; withholding the decision does
    # not strand it in the standoff hold.
    assert engine.assets["UAV-STRIKE-01"]["_current_behavior"] == "post_launch_carrier_recovery"

    threat = engine.threats["MOBILE-COASTAL-AD-01"]
    closest = min(
        _nm(lat, lng, float(threat["lat"]), float(threat["lng"]))
        for lat, lng in samples["LOITER-UAV-01"]
    )
    assert closest >= 5.0, closest

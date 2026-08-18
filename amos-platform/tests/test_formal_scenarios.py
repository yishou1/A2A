from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from amos_platform.agents.a2a.mapper import build_commander_workflow_payload
from amos_platform.data.scenario_repository import get_scenario
from amos_platform.sensors.coverage import distance_nm
from amos_platform.simulation.engine import SimEngine, _advance_position


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_IDS = (
    "amphibious-landing-joint-operation",
    "border-uav-evacuation",
    "maritime-convoy-air-defense",
)

OBSERVATION_GATES = {
    "amphibious-landing-joint-operation": [(450, 1), (780, 2), (1110, 3)],
    "border-uav-evacuation": [(420, 1), (780, 2)],
    # AEW collection starts one minute before the T+720 PPI product so the
    # evidence contains a real 60-second observation window.
    "maritime-convoy-air-defense": [(660, 2)],
}

EVIDENCE_SENSOR_CHECKS = (
    ("amphibious-landing-joint-operation", 780, "DDG-01", 1),
    ("border-uav-evacuation", 1080, "MOUNTAIN-RADAR-01", 2),
    ("maritime-convoy-air-defense", 720, "AEW-01", 2),
)


def _inside_ao(lat: float, lng: float, ao: dict) -> bool:
    return ao["south"] <= lat <= ao["north"] and ao["west"] <= lng <= ao["east"]


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_formal_scenario_story_and_media_are_complete(scenario_id: str) -> None:
    scenario = get_scenario(scenario_id)
    assert scenario is not None
    assert len(scenario["assets"]) >= 6
    assert len(scenario["threats"]) >= 2
    assert len(scenario["timeline"]) >= 8
    assert len(scenario["media_cues"]) >= 8
    assert [row["at_sec"] for row in scenario["timeline"]] == sorted(
        row["at_sec"] for row in scenario["timeline"]
    )
    assert [row["at_sec"] for row in scenario["media_cues"]] == sorted(
        row["at_sec"] for row in scenario["media_cues"]
    )
    assert {row["phase"] for row in scenario["timeline"]} >= {
        "FIND", "FIX", "TRACK", "TARGET", "ENGAGE", "ASSESS"
    }
    assert all(row["risk_level"] == "UNKNOWN" for row in scenario["threats"])

    for item in scenario["media_cues"]:
        media_path = ROOT / item["uri"].removeprefix("/")
        assert media_path.is_file(), media_path
        assert hashlib.sha256(media_path.read_bytes()).hexdigest() == item["checksum"]
        assert item["provenance"]["mode"] == "simulation"


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_formal_scenarios_use_taiwan_area_and_keep_all_geometry_inside_ao(
    scenario_id: str,
) -> None:
    scenario = get_scenario(scenario_id)
    assert scenario is not None
    theater = scenario["theater"]
    center = theater["center"]
    ao = theater["ao"]

    assert 21.5 <= center["lat"] <= 24.5
    assert 119.5 <= center["lng"] <= 122.2
    assert "台湾" in theater["name"]
    assert theater["location_profile"] == "fictional_training_area"
    assert _inside_ao(center["lat"], center["lng"], ao)

    for asset in scenario["assets"]:
        pos = asset["position"]
        assert _inside_ao(pos["lat"], pos["lng"], ao), asset["asset_id"]
    for threat in scenario["threats"]:
        pos = threat["position"]
        assert _inside_ao(pos["lat"], pos["lng"], ao), threat["threat_id"]
    for asset_id, route in scenario["asset_routes"].items():
        assert all(_inside_ao(point["lat"], point["lng"], ao) for point in route), asset_id
    for protected in scenario["protected_assets"]:
        assert _inside_ao(protected["lat"], protected["lon"], ao)
        assert protected["metadata"]["location_profile"] == "fictional_training_area"

    expected_surface = "land" if scenario_id == "border-uav-evacuation" else "coastal"
    assert scenario["map_display"]["base_surface"] == expected_surface


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_commander_payload_contains_only_released_media_and_scenario_plan(scenario_id: str) -> None:
    scenario = get_scenario(scenario_id)
    assert scenario is not None
    release_times = [float(row["at_sec"]) for row in scenario["media_cues"]]
    for elapsed in release_times:
        payload = build_commander_workflow_payload(
            scenario,
            {"clock": {"elapsed_sec": elapsed, "run_id": "run-causal"}},
            {},
            workflow_mode="bpel",
            workflow_file="integrated_system/workflows/integrated_demo_workflow.bpel",
        )
        declared = {row["media_id"]: float(row["at_sec"]) for row in scenario["media_cues"]}
        assert payload["attachments"]
        assert all(declared[item["id"]] <= elapsed for item in payload["attachments"])
        assert payload["attachments"][-1]["id"] in {
            row["media_id"] for row in scenario["media_cues"] if row["at_sec"] == elapsed
        }
        assert [row["agent_id"] for row in payload["functional_agents"]] == [
            "A1", "A2", "A3", "A4", "A5", "A6"
        ]
        assert len(payload["function_point_coverage"]) == 29


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_formal_scenario_assets_move_and_clock_stops_at_duration(scenario_id: str) -> None:
    scenario = get_scenario(scenario_id)
    assert scenario is not None
    engine = SimEngine()
    engine.load_scenario(scenario)
    initial = {asset_id: dict(asset["position"]) for asset_id, asset in engine.assets.items()}
    fixed = {asset_id for asset_id, route in scenario["asset_routes"].items() if not route}
    windows = scenario.get("asset_motion_windows") or {}
    mobile = {
        asset_id for asset_id, route in scenario["asset_routes"].items()
        if route and float((windows.get(asset_id) or {}).get("start_sec", 0) or 0) <= 100
    }
    standby = {
        asset_id for asset_id, route in scenario["asset_routes"].items()
        if route and float((windows.get(asset_id) or {}).get("start_sec", 0) or 0) > 100
    }

    for _ in range(20):
        engine._tick(5)

    assert all(engine.assets[asset_id]["position"] == initial[asset_id] for asset_id in fixed)
    assert all(engine.assets[asset_id]["position"] != initial[asset_id] for asset_id in mobile)
    assert all(engine.assets[asset_id]["position"] == initial[asset_id] for asset_id in standby)
    duration = float(scenario["demo_controls"]["duration_sec"])
    engine.clock["running"] = True
    engine._tick(duration + 1)
    assert engine.clock["elapsed_sec"] == duration
    assert engine.clock["running"] is False


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_formal_scenario_route_length_matches_speed_and_motion_window(scenario_id: str) -> None:
    scenario = get_scenario(scenario_id)
    assert scenario is not None
    duration = float(scenario["demo_controls"]["duration_sec"])
    assets = {row["asset_id"]: row for row in scenario["assets"]}
    modes = scenario.get("asset_route_modes") or {}
    windows = scenario.get("asset_motion_windows") or {}

    for asset_id, route in scenario["asset_routes"].items():
        assert modes.get(asset_id) in {"hold", "loop"}
        if not route:
            continue
        asset = assets[asset_id]
        start = asset["position"]
        points = [{"lat": start["lat"], "lng": start["lng"]}, *route]
        route_nm = sum(
            distance_nm(a["lat"], a["lng"], b["lat"], b["lng"])
            for a, b in zip(points, points[1:])
        )
        window = windows.get(asset_id) or {}
        start_sec = float(window.get("start_sec", 0) or 0)
        end_sec = float(window.get("end_sec", duration) or duration)
        assert 0 <= start_sec < end_sec <= duration
        reachable_nm = float(asset["speed_kts"]) * (end_sec - start_sec) / 3600.0
        assert reachable_nm > 0
        if modes[asset_id] == "hold":
            assert route_nm <= reachable_nm * 1.05, (
                scenario_id, asset_id, route_nm, reachable_nm
            )


def test_knot_speed_uses_simulated_seconds_and_latitude_corrected_longitude() -> None:
    start_lat, start_lng = 23.5, 121.2
    end_lat, end_lng = _advance_position(start_lat, start_lng, 90, 20 * 360 / 3600)
    assert distance_nm(start_lat, start_lng, end_lat, end_lng) == pytest.approx(2.0, abs=0.01)


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_full_scenario_motion_remains_in_ao_and_never_projects_future_routes(
    scenario_id: str,
) -> None:
    scenario = get_scenario(scenario_id)
    assert scenario is not None
    engine = SimEngine()
    engine.load_scenario(scenario)
    ao = scenario["theater"]["ao"]
    duration = float(scenario["demo_controls"]["duration_sec"])
    fixed_initial = {
        asset_id: dict(engine.assets[asset_id]["position"])
        for asset_id, route in scenario["asset_routes"].items() if not route
    }

    while engine.clock["elapsed_sec"] < duration:
        engine._tick(min(30, duration - engine.clock["elapsed_sec"]))
        assert all(
            _inside_ao(row["position"]["lat"], row["position"]["lng"], ao)
            for row in engine.assets.values()
        )
        assert all(_inside_ao(row["lat"], row["lng"], ao) for row in engine.threats.values())

    assert all(engine.assets[asset_id]["position"] == position for asset_id, position in fixed_initial.items())
    assert set(engine.waypoint_nav.get_all()) == set(scenario["asset_routes"])


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_air_assets_publish_real_altitude_and_story_fits_duration(scenario_id: str) -> None:
    scenario = get_scenario(scenario_id)
    assert scenario is not None
    duration = float(scenario["demo_controls"]["duration_sec"])
    assert all(
        float(asset["position"].get("alt_ft", 0)) > 0
        for asset in scenario["assets"] if asset["domain"] == "air"
    )
    assert max(row["at_sec"] for row in scenario["timeline"]) <= duration
    assert max(row["at_sec"] for row in scenario["media_cues"]) <= duration
    assert max(row["min_elapsed_sec"] for row in scenario["demo_checkpoints"]) <= duration


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_contacts_are_observed_only_when_their_current_sensor_evidence_is_due(
    scenario_id: str,
) -> None:
    scenario = get_scenario(scenario_id)
    assert scenario is not None
    engine = SimEngine()
    engine.load_scenario(scenario)
    gates = OBSERVATION_GATES[scenario_id]

    first_gate = gates[0][0]
    while engine.clock["elapsed_sec"] < first_gate - 5:
        engine._tick(min(30, first_gate - 5 - engine.clock["elapsed_sec"]))
    assert not engine.sensor_fusion.get_tracks()

    for gate_sec, expected_tracks in gates:
        while engine.clock["elapsed_sec"] < gate_sec + 10:
            engine._tick(min(5, gate_sec + 10 - engine.clock["elapsed_sec"]))
        assert len(engine.sensor_fusion.get_tracks()) >= expected_tracks


@pytest.mark.parametrize(
    "scenario_id,at_sec,sensor_asset_id,minimum_observations",
    EVIDENCE_SENSOR_CHECKS,
)
def test_instrument_media_has_matching_sensor_observations(
    scenario_id: str,
    at_sec: int,
    sensor_asset_id: str,
    minimum_observations: int,
) -> None:
    engine = SimEngine()
    engine.load_scenario(get_scenario(scenario_id))
    while engine.clock["elapsed_sec"] < at_sec:
        engine._tick(min(5, at_sec - engine.clock["elapsed_sec"]))
    current_observations = engine.sensor_fusion.last_observation_batch["observations"]
    assert sum(row["asset_id"] == sensor_asset_id for row in current_observations) >= minimum_observations


def test_amphibious_scenario_has_three_threat_domains_and_joint_resources() -> None:
    scenario = get_scenario("amphibious-landing-joint-operation")
    assert scenario is not None
    assert {row["threat_id"] for row in scenario["threats"]} == {
        "CONTACT-SURFACE-FAST", "CONTACT-FORTIFICATION-01", "CONTACT-EMITTER-01"
    }
    assert {row["asset_id"] for row in scenario["assets"]} >= {
        "LANDING-01", "DDG-01", "LOITER-01", "HELO-01", "ARTY-01"
    }
    assert scenario["acceptance_profile"]["required_loop_event"] == "re_attack_required"


def test_border_scenario_has_relay_failure_and_evacuation_constraints() -> None:
    scenario = get_scenario("border-uav-evacuation")
    assert scenario is not None
    assert {row["asset_id"] for row in scenario["assets"]} >= {
        "RELAY-UAV-01", "MOUNTAIN-RADAR-01", "GROUND-PATROL-01"
    }
    assert {row["branch_id"] for row in scenario["expected_branches"]} >= {
        "communication_degraded", "agent_failure", "low_confidence"
    }
    assert scenario["environment"]["civilian_area"]
    assert scenario["environment"]["restricted_area"]


def test_maritime_scenario_has_two_unknown_surface_targets_and_engagement_policy() -> None:
    scenario = get_scenario("maritime-convoy-air-defense")
    assert scenario is not None
    assert {row["asset_id"] for row in scenario["assets"]} >= {
        "MERCHANT-01", "MERCHANT-02", "ESCORT-01", "AEW-01", "SHORE-RADAR-01"
    }
    assert {row["threat_id"] for row in scenario["threats"]} == {
        "CONTACT-HOSTILE-01", "CONTACT-FISHING-01",
    }
    assert {row["domain"] for row in scenario["threats"]} == {"maritime"}
    assert next(
        row for row in scenario["threats"]
        if row["threat_id"] == "CONTACT-FISHING-01"
    )["ais_match"] is True
    escort = next(row for row in scenario["assets"] if row["asset_id"] == "ESCORT-01")
    reconnaissance_uav = next(row for row in scenario["assets"] if row["asset_id"] == "AEW-01")
    assert escort["weapons"] == ["舰载反舰导弹"]
    assert reconnaissance_uav["role"] == "侦察无人机"
    assert len(scenario["asset_routes"]["AEW-01"]) == 8
    assert scenario["asset_route_modes"]["AEW-01"] == "loop"
    assert scenario["engagement_policy"]["protected_truth_ids"] == ["CONTACT-FISHING-01"]
    assert scenario["engagement_policy"]["requires_prior_warning"] is True
    assert scenario["engagement_policy"]["warning_delay_sec"] == 300
    assert scenario["acceptance_profile"]["requires_explicit_fire_authorization"] is True
    assert {row["branch_id"] for row in scenario["expected_branches"]} >= {
        "resource_unavailable", "behavior_changed"
    }

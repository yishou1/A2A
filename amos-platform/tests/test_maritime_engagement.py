from __future__ import annotations

from amos_platform.api.app_factory import create_app
from amos_platform.data.scenario_repository import get_scenario
from amos_platform.simulation.engine import SimEngine


SCENARIO_ID = "maritime-convoy-air-defense"


def _identified_tracks() -> tuple[SimEngine, object, object]:
    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)
    engine.clock.update({"run_id": "run-engagement", "scenario_id": SCENARIO_ID})
    engine._tick(660)
    assert len(engine.sensor_fusion.tracks) == 2

    by_truth = {
        engine._truth_target_for_track(track): track
        for track in engine.sensor_fusion.tracks.values()
    }
    hostile = by_truth["CONTACT-HOSTILE-01"]
    fishing = by_truth["CONTACT-FISHING-01"]

    hostile.classification = "FAST_ATTACK_CRAFT"
    hostile.threat_level = "HIGH"
    hostile.kill_chain_phase = "TARGET"
    hostile.agent_assessment = {
        "status": "confirmed",
        "label": "高风险",
        "source": "A2A 后端工作流",
    }
    fishing.classification = "FISHING_VESSEL"
    fishing.threat_level = "HIGH"
    fishing.kill_chain_phase = "TARGET"
    fishing.agent_assessment = {
        "status": "confirmed",
        "label": "渔船",
        "source": "A2A 后端工作流",
    }
    return engine, hostile, fishing


def test_fire_command_requires_identification_and_explicit_authorization() -> None:
    engine, hostile, _ = _identified_tracks()

    rejected = engine.fire_weapon_at_track(
        hostile.id,
        asset_id="ESCORT-01",
        weapon_name="舰载反舰导弹",
        authorized=False,
    )
    launched = engine.fire_weapon_at_track(
        hostile.id,
        asset_id="ESCORT-01",
        weapon_name="舰载反舰导弹",
        authorized=True,
    )

    assert "error" in rejected
    assert launched["status"] == "launched"
    assert launched["track_id"] == hostile.id
    assert launched["authorization"] == "operator_confirmed"
    assert "threat_id" not in launched
    assert hostile.kill_chain_phase == "ENGAGE"

    public_weapon = engine.get_operator_state()["weapons"][0]
    assert public_weapon["target_track_id"] == hostile.id
    assert "target_threat_id" not in public_weapon
    assert "p_kill" not in public_weapon
    launch_alert = engine.get_operator_state()["alerts"][-1]
    assert "海面接触 " in launch_alert["msg"]
    assert "高速攻击艇" in launch_alert["msg"]
    assert "待识别接触" not in launch_alert["msg"]


def test_weapon_impact_is_detected_when_director_uses_large_steps() -> None:
    engine, hostile, _ = _identified_tracks()
    launched = engine.fire_weapon_at_track(
        hostile.id,
        asset_id="ESCORT-01",
        weapon_name="舰载反舰导弹",
        authorized=True,
    )
    engine.weapons[launched["weapon_id"]]["p_kill"] = 0.0

    for _ in range(4):
        engine._tick(30.0)
        if engine.weapons[launched["weapon_id"]]["status"] != "in_flight":
            break

    weapon = engine.weapons[launched["weapon_id"]]
    assert weapon["status"] == "hit"
    assert weapon["damage_state"] == "destroyed"


def test_post_strike_capture_observes_destroyed_target_without_recreating_track() -> None:
    engine, hostile, _ = _identified_tracks()
    engine._tick(3630.0 - float(engine.clock["elapsed_sec"]))
    by_truth = {
        engine._truth_target_for_track(track): track
        for track in engine.sensor_fusion.tracks.values()
    }
    hostile = by_truth["CONTACT-HOSTILE-01"]
    hostile.classification = "FAST_ATTACK_CRAFT"
    hostile.threat_level = "HIGH"
    hostile.kill_chain_phase = "TARGET"
    hostile.agent_assessment = {
        "status": "confirmed",
        "label": "高风险",
        "source": "test",
    }
    truth_id = engine._truth_target_for_track(hostile)
    assert truth_id is not None
    engine._tick(1)
    assert engine.get_operator_state()["follow_launch_prompt"]["track_id"] == hostile.id
    authorized = engine.authorize_follow_asset(
        "UAV-CONFIRM-01",
        hostile.id,
        authorized=True,
    )
    assert authorized["status"] == "authorized"
    engine._tick(5580.0 - float(engine.clock["elapsed_sec"]))
    assert "MAR-MEDIA-07" not in engine.media_capture.captured_media_ids

    engine._apply_damage(engine.threats[truth_id], "destroyed")
    engine.sensor_fusion.tracks.pop(hostile.id, None)
    engine._tick(1.0)

    assert "MAR-MEDIA-07" in engine.media_capture.captured_media_ids
    assert hostile.id not in engine.sensor_fusion.tracks


def test_fishing_vessel_remains_no_strike_even_if_risk_is_overstated() -> None:
    engine, _, fishing = _identified_tracks()

    result = engine.fire_weapon_at_track(
        fishing.id,
        asset_id="ESCORT-01",
        weapon_name="舰载反舰导弹",
        authorized=True,
    )

    assert result == {"error": "目标身份未知或属于民用禁射类别"}
    assert engine.weapons == {}


def test_operator_projection_exposes_only_safe_engagement_fields() -> None:
    engine, hostile, fishing = _identified_tracks()

    tracks = {
        item["id"]: item
        for item in engine.get_operator_state()["fused_tracks"]
    }
    hostile_public = tracks[hostile.id]
    fishing_public = tracks[fishing.id]

    assert hostile_public["assessment_level"] == "HIGH"
    assert hostile_public["display_label"].startswith("海面接触 ")
    assert hostile_public["kill_chain_phase"] == "TARGET"
    assert hostile_public["engagement_eligible"] is True
    assert fishing_public["assessment_level"] == "LOW"
    assert fishing_public["engagement_eligible"] is False
    assert fishing_public["engagement_block_reason"] == "目标身份未知或属于民用禁射类别"
    assert "threat_level" not in hostile_public
    assert "kill_chain" not in hostile_public


def test_damage_alert_uses_the_same_operator_contact_label_as_the_map() -> None:
    engine, hostile, _ = _identified_tracks()
    truth_id = engine._truth_target_for_track(hostile)
    assert truth_id is not None
    public_track = next(
        item for item in engine.get_operator_state()["fused_tracks"]
        if item["id"] == hostile.id
    )

    engine._apply_damage(engine.threats[truth_id], "destroyed")
    public_alert = engine.get_operator_state()["alerts"][-1]

    assert public_track["display_label"] in public_alert["msg"]
    assert "高速攻击艇" in public_alert["msg"]
    assert "待识别接触" not in public_alert["msg"]
    assert public_alert["target_track_id"] == hostile.id


def test_operator_contact_labels_remain_stable_when_another_track_disappears() -> None:
    engine, hostile, fishing = _identified_tracks()
    initial = {
        track["id"]: track["display_label"]
        for track in engine.get_operator_state()["fused_tracks"]
    }

    engine.sensor_fusion.tracks.pop(hostile.id)
    remaining = {
        track["id"]: track["display_label"]
        for track in engine.get_operator_state()["fused_tracks"]
    }

    assert remaining[fishing.id] == initial[fishing.id]


def test_confirm_uav_launches_from_escort_and_follows_confirmed_high_threat() -> None:
    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)

    escort_pos = engine.assets["ESCORT-01"]["position"]
    uav_pos = engine.assets["UAV-CONFIRM-01"]["position"]
    assert (uav_pos["lat"], uav_pos["lng"]) == (escort_pos["lat"], escort_pos["lng"])
    assert engine.assets["UAV-CONFIRM-01"]["status"] == "staged"
    assert not any(
        asset["id"] == "UAV-CONFIRM-01"
        for asset in engine.get_operator_state()["assets"]
    )

    engine._tick(2160)
    assert engine.assets["UAV-CONFIRM-01"]["status"] == "staged"
    by_truth = {
        engine._truth_target_for_track(track): track
        for track in engine.sensor_fusion.tracks.values()
    }
    hostile = by_truth["CONTACT-HOSTILE-01"]
    hostile.classification = "FAST_ATTACK_CRAFT"
    hostile.threat_level = "HIGH"
    hostile.agent_assessment = {"status": "confirmed", "source": "test"}
    engine.set_speed(8)

    engine._tick(1)

    prompt_state = engine.get_operator_state()
    prompt = prompt_state["follow_launch_prompt"]
    assert prompt["prompt_type"] == "launch_follow_uav"
    assert prompt["asset_id"] == "UAV-CONFIRM-01"
    assert prompt["track_id"] == hostile.id
    assert prompt_state["clock"]["speed"] == 1
    assert prompt_state["clock"]["speed_locked_reason"] == "awaiting_follow_confirmation"
    assert prompt_state["clock"]["speed_resume_value"] == 8
    engine.set_speed(32)
    assert engine.clock["speed"] == 1
    assert engine.assets["UAV-CONFIRM-01"]["status"] == "staged"
    assert not any(
        asset["id"] == "UAV-CONFIRM-01"
        for asset in prompt_state["assets"]
    )

    escort_before_launch = dict(engine.assets["ESCORT-01"]["position"])
    authorized = engine.authorize_follow_asset(
        "UAV-CONFIRM-01",
        hostile.id,
        authorized=True,
    )
    assert authorized["status"] == "authorized"
    assert engine.clock["speed"] == 8
    assert "speed_locked_reason" not in engine.clock

    uav = engine.assets["UAV-CONFIRM-01"]
    launch_distance_nm = engine.waypoint_nav._haversine(
        escort_before_launch["lat"],
        escort_before_launch["lng"],
        uav["position"]["lat"],
        uav["position"]["lng"],
    )
    route = engine.waypoint_nav.get_route("UAV-CONFIRM-01")
    assert uav["status"] == "active"
    assert launch_distance_nm < 0.1
    assert uav["_follow_track_id"] == hostile.id
    assert route and route[0]["label"] == "FOLLOW"
    station_bearing = engine.waypoint_nav._bearing(
        hostile.lat, hostile.lng, route[0]["lat"], route[0]["lng"],
    )
    expected_bearing = ((hostile.heading_deg or station_bearing) + 180.0) % 360.0
    assert abs((station_bearing - expected_bearing + 180.0) % 360.0 - 180.0) < 1.0
    assert any(
        asset["id"] == "UAV-CONFIRM-01"
        for asset in engine.get_operator_state()["assets"]
    )

    uav["status"] = "holding"
    engine._tick(1)
    assert uav["status"] == "active"
    assert engine.waypoint_nav.get_route("UAV-CONFIRM-01")[0]["label"] == "FOLLOW"


def test_follow_uav_keeps_a_stable_trailing_station_and_slows_near_it() -> None:
    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)
    engine._tick(2160)
    hostile = {
        engine._truth_target_for_track(track): track
        for track in engine.sensor_fusion.tracks.values()
    }["CONTACT-HOSTILE-01"]
    hostile.classification = "FAST_ATTACK_CRAFT"
    hostile.threat_level = "HIGH"
    hostile.heading_deg = 90.0
    hostile.agent_assessment = {"status": "confirmed", "source": "test"}
    engine._tick(1)
    engine.authorize_follow_asset("UAV-CONFIRM-01", hostile.id, authorized=True)

    first_station = engine.waypoint_nav.get_route("UAV-CONFIRM-01")[0]
    uav = engine.assets["UAV-CONFIRM-01"]
    uav["position"].update({"lat": hostile.lat, "lng": hostile.lng + 0.02})
    engine._update_asset_follow_tasks()
    second_station = engine.waypoint_nav.get_route("UAV-CONFIRM-01")[0]

    assert engine.waypoint_nav._haversine(
        first_station["lat"], first_station["lng"],
        second_station["lat"], second_station["lng"],
    ) < 0.05
    uav["position"].update({"lat": second_station["lat"], "lng": second_station["lng"]})
    engine._update_asset_follow_tasks()
    assert 1.0 <= uav["speed_kts"] < uav["_cruise_speed_kts"]


def test_confirm_uav_returns_to_escort_and_hides_after_authorized_strike() -> None:
    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)
    engine.clock.update({"run_id": "run-engagement", "scenario_id": SCENARIO_ID})
    engine._tick(2160)
    by_truth = {
        engine._truth_target_for_track(track): track
        for track in engine.sensor_fusion.tracks.values()
    }
    hostile = by_truth["CONTACT-HOSTILE-01"]
    hostile.classification = "FAST_ATTACK_CRAFT"
    hostile.threat_level = "HIGH"
    hostile.kill_chain_phase = "TARGET"
    hostile.agent_assessment = {
        "status": "confirmed",
        "label": "high",
        "source": "test",
    }
    escort_before_launch = dict(engine.assets["ESCORT-01"]["position"])
    engine._tick(1)
    assert engine.get_operator_state()["follow_launch_prompt"]["track_id"] == hostile.id
    authorized = engine.authorize_follow_asset(
        "UAV-CONFIRM-01",
        hostile.id,
        authorized=True,
    )
    assert authorized["status"] == "authorized"
    uav = engine.assets["UAV-CONFIRM-01"]
    assert uav.get("_operator_follow_visible") is True

    launched = engine.fire_weapon_at_track(
        hostile.id,
        asset_id="ESCORT-01",
        weapon_name="舰载反舰导弹",
        authorized=True,
    )
    for _ in range(6):
        engine._tick(30.0)
        if engine.weapons[launched["weapon_id"]]["status"] != "in_flight":
            break

    assert engine.weapons[launched["weapon_id"]]["damage_state"] == "destroyed"
    assert uav.get("_follow_return_pending_track_id") == hostile.id
    assert not uav.get("_follow_returning_home")
    assert engine.waypoint_nav.get_route("UAV-CONFIRM-01")[0]["label"] == "FOLLOW"

    engine.clock["elapsed_sec"] = 5609.0
    engine._tick(1.0)
    assert uav.get("_follow_returning_home") is True
    assert engine.waypoint_nav.get_route("UAV-CONFIRM-01")[0]["label"] == "RETURN"

    escort_current = engine.assets["ESCORT-01"]["position"]
    uav["position"]["lat"] = escort_current["lat"]
    uav["position"]["lng"] = escort_current["lng"]
    engine._tick(1)

    assert uav["status"] == "staged"
    assert uav.get("_operator_follow_visible") is False
    assert not any(
        asset["id"] == "UAV-CONFIRM-01"
        for asset in engine.get_operator_state()["assets"]
    )


def test_fire_command_api_applies_the_same_server_side_gates(monkeypatch) -> None:
    from amos_platform.api.routes import sim_routes

    engine, hostile, fishing = _identified_tracks()
    monkeypatch.setattr(sim_routes, "get_engine", lambda: engine)
    app = create_app()
    app.testing = True
    client = app.test_client()

    denied = client.post("/api/v1/sim/commands", json={
        "command_type": "fire",
        "params": {
            "track_id": fishing.id,
            "asset_id": "ESCORT-01",
            "weapon_name": "舰载反舰导弹",
        },
        "authorization": {"approved": True},
    })
    accepted = client.post("/api/v1/sim/commands", json={
        "command_type": "fire",
        "params": {
            "track_id": hostile.id,
            "asset_id": "ESCORT-01",
            "weapon_name": "舰载反舰导弹",
        },
        "authorization": {"approved": True},
    })

    assert denied.status_code == 409
    assert accepted.status_code == 200
    result = accepted.get_json()["data"]["result"]
    assert result["track_id"] == hostile.id
    assert result["authorization"] == "operator_confirmed"


def test_launch_follow_uav_command_authorizes_backend_follow_task(monkeypatch) -> None:
    from amos_platform.api.routes import sim_routes

    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)
    engine._tick(2160)
    by_truth = {
        engine._truth_target_for_track(track): track
        for track in engine.sensor_fusion.tracks.values()
    }
    hostile = by_truth["CONTACT-HOSTILE-01"]
    hostile.classification = "FAST_ATTACK_CRAFT"
    hostile.threat_level = "HIGH"
    hostile.agent_assessment = {"status": "confirmed", "source": "test"}
    engine._tick(1)
    assert engine.get_operator_state()["follow_launch_prompt"]["track_id"] == hostile.id
    monkeypatch.setattr(sim_routes, "get_engine", lambda: engine)

    app = create_app()
    app.testing = True
    accepted = app.test_client().post("/api/v1/sim/commands", json={
        "command_type": "launch_follow_uav",
        "params": {
            "track_id": hostile.id,
            "asset_id": "UAV-CONFIRM-01",
        },
        "authorization": {"approved": True},
    })

    assert accepted.status_code == 200
    result = accepted.get_json()["data"]["result"]
    assert result["track_id"] == hostile.id
    assert result["asset_id"] == "UAV-CONFIRM-01"
    assert engine.assets["UAV-CONFIRM-01"]["status"] == "active"
    assert engine.waypoint_nav.get_route("UAV-CONFIRM-01")[0]["label"] == "FOLLOW"


def test_public_scenario_does_not_reveal_private_no_strike_truth_ids() -> None:
    app = create_app()
    app.testing = True
    payload = app.test_client().get(f"/api/v1/scenarios/{SCENARIO_ID}").get_json()["data"]

    assert "engagement_policy" not in payload
    assert "protected_truth_ids" not in str(payload)
    assert "CONTACT-FISHING-01" not in str(payload)

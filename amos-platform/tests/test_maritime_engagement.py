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
    assert weapon["damage_state"] == "damaged"


def test_post_strike_capture_observes_destroyed_target_without_recreating_track() -> None:
    engine, hostile, _ = _identified_tracks()
    truth_id = engine._truth_target_for_track(hostile)
    assert truth_id is not None
    engine._tick(3630.0 - float(engine.clock["elapsed_sec"]))
    engine._apply_damage(engine.threats[truth_id], "destroyed")
    engine.sensor_fusion.tracks.pop(hostile.id, None)
    engine._tick(5610.0 - float(engine.clock["elapsed_sec"]))

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
    assert fishing_public["assessment_level"] == "HIGH"
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


def test_public_scenario_does_not_reveal_private_no_strike_truth_ids() -> None:
    app = create_app()
    app.testing = True
    payload = app.test_client().get(f"/api/v1/scenarios/{SCENARIO_ID}").get_json()["data"]

    assert "engagement_policy" not in payload
    assert "protected_truth_ids" not in str(payload)
    assert "CONTACT-FISHING-01" not in str(payload)

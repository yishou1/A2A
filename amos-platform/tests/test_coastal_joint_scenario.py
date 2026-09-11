from __future__ import annotations

from copy import deepcopy

from amos_platform.agents.a2a.commander_projection import apply_commander_assessments
from amos_platform.agents.commander_bridge import CommanderBridge
from amos_platform.data.scenario_repository import get_scenario
from amos_platform.domain.policies.visibility import find_truth_leaks
from amos_platform.sensors.coverage import distance_nm
from amos_platform.simulation.engine import SimEngine


SCENARIO_ID = "coastal-joint-recon-strike"


def _advance(engine: SimEngine, elapsed_sec: float) -> None:
    while float(engine.clock["elapsed_sec"]) < elapsed_sec:
        engine._tick(min(30, elapsed_sec - float(engine.clock["elapsed_sec"])))


def _apply_ground_site_assessment(engine: SimEngine, workflow_id: str = "wf-cjr") -> str:
    track = next(iter(engine.sensor_fusion.tracks.values()))
    projection = apply_commander_assessments(
        engine,
        {
            "workflow_id": workflow_id,
            "status": "completed",
            "result": {
                "outputs": {
                    "tracking_result": [{"value": {"tracks": [{
                        "track_id": track.id,
                        "object_type": "ground_installation",
                        "metadata": {
                            "source_class": "coastal_missile_site",
                            "label": "hostile",
                            "affiliation": "red",
                            "threat_level": "high",
                        },
                        "lat": track.lat,
                        "lon": track.lng,
                    }]}}],
                    "threat_assessment_result": [{"value": {"threats": [{
                        "track_id": track.id,
                        "score": 0.94,
                        "level": "high",
                    }]}}],
                },
                "summary": {"verification": "ground-site backend fixture"},
            },
        },
        submission={
            "run_id": str(engine.clock.get("run_id") or "run-cjr-test"),
            "transport": "gateway",
            "package": {"package_id": "pkg-cjr", "verified": True},
            "snapshot_sequence": int(engine.sensor_fusion.last_observation_batch.get("tick_id", 0) or 0),
            "simulation_time_sec": float(engine.clock["elapsed_sec"]),
        },
    )
    assert projection["status"] == "completed"
    assert projection["applied_count"] == 1
    return track.id


def test_coastal_joint_force_package_and_backend_contract_are_complete() -> None:
    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    assert {item["asset_id"] for item in scenario["assets"]} == {
        "SAT-RECON-01",
        "SAT-RECON-02",
        "SAT-COM-01",
        "SEA-C2-01",
        "WZ10-01",
        "J16-01",
        "ATTACK-UAV-01",
        "ATTACK-UAV-02",
    }
    assert {item["agent_id"] for item in scenario["functional_agents"]} == {
        "A1", "A2", "A3", "A4", "A5", "A6",
    }
    assert len(scenario["algorithm_coverage"]) == 19
    assert len(scenario["function_point_coverage"]) == 28
    assert len(scenario["coordination_links"]) == 11
    assert len(scenario["engagement_policy"]["coordinated_engagement"]["participants"]) == 4
    assert len(scenario["media_cues"]) == 12
    assert scenario["map_display"]["space_visual_speed_factor"] == 0.035
    assert "SAT-RECON-01" not in scenario["map_display"]["trail_asset_ids"]
    assert len(scenario["map_display"]["space_ground_tracks"]) == 2
    assert scenario["map_display"]["space_node_asset_ids"] == ["SAT-COM-01"]
    assert len(scenario["space_operations"]["passes"]) == 2
    assert scenario["space_operations"]["relay"]["asset_id"] == "SAT-COM-01"
    media_by_id = {item["media_id"]: item for item in scenario["media_cues"]}
    assert {
        media_by_id[media_id]["sensor_id"]
        for media_id in ("CJR-MEDIA-08", "CJR-MEDIA-09")
    } == {"ATTACK-UAV-01/EO-IR", "ATTACK-UAV-02/EO-IR"}
    assert media_by_id["CJR-MEDIA-10"]["sensor_id"] == "WZ10-01/ELINT"
    assert media_by_id["CJR-MEDIA-11"]["sensor_id"] == "SEA-C2-01/DATALINK-TRANSFER"
    capture_by_media = {
        item["media_id"]: item for item in scenario["capture_plans"]
    }
    assert capture_by_media["CJR-MEDIA-11"]["capture_parameters"]["required_source_media_ids"] == [
        "CJR-MEDIA-01", "CJR-MEDIA-02", "CJR-MEDIA-03", "CJR-MEDIA-10",
    ]
    assert all(
        int(item.get("member_count", 1) or 1) == 1
        for item in scenario["asset_profiles"]
    )
    assert all(
        marker not in str(item.get("role") or "")
        for item in scenario["assets"]
        for marker in ("群", "编队", "蜂群")
    )

    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)
    _advance(engine, 1530)
    payload = CommanderBridge().build_workflow_payload(scenario, {}, engine)
    mission = payload["attachments"][0]["meta"]["amos_mission"]
    assert mission["mission_type"] == "coastal_joint_reconnaissance_and_strike"
    # T+25:30 前卫星已驶离本地视窗、攻击无人机仍在舰面待命，
    # 任务包只应携带当下实际在场的平台，而非把尚未起飞的资源伪装为空中资产。
    assert {item["platform_id"] for item in mission["friendly_platforms"]} == {
        "SAT-COM-01", "SEA-C2-01", "WZ10-01", "J16-01",
    }
    assert all(
        int(item["metadata"].get("member_count", 1) or 1) == 1
        for item in mission["friendly_platforms"]
    )
    assert len(mission["contacts"]) == 1
    assert mission["contacts"][0]["kind"] == "ground-contact"
    assert mission["contacts"][0]["metadata"]["domain_hint"] == "ground"
    assert mission["contacts"][0]["metadata"]["speed_kts"] <= 5
    assert mission["environment"]["coordination_links"]
    satellite_links = [
        link for link in engine.mesh.links.values()
        if "SAT-COM-01" in {link["from"], link["to"]}
    ]
    assert satellite_links
    assert all("SATCOM" in link["band"] for link in satellite_links)
    assert min(link["distance_km"] for link in satellite_links) > 450


def test_backend_assessment_unlocks_four_individual_weapon_nodes_without_truth_leak() -> None:
    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)
    engine.clock["run_id"] = "run-cjr-test"
    _advance(engine, 3340)
    track_id = _apply_ground_site_assessment(engine)

    operator = engine.get_operator_state()
    target = next(item for item in operator["fused_tracks"] if item["id"] == track_id)
    assert target["classification"] == "COASTAL_MISSILE_SITE"
    assert target["engagement_eligible"] is True
    assert operator["engagement_action"]["coordinated"] is True
    assert len(operator["engagement_action"]["participants"]) == 4
    assert target["engagement_action"]["coordinated"] is True
    assert target["engagement_action"]["coordination_chain_id"] == "CJR-WEAPON-CHAIN-01"
    assert len(target["engagement_action"]["participants"]) == 4

    launched = engine.fire_weapon_at_track(
        track_id,
        asset_id="SEA-C2-01",
        weapon_name="舰载对陆巡航导弹",
        authorized=True,
    )
    assert launched["status"] == "launched"
    assert launched["coordinated"] is True
    assert len(launched["weapon_ids"]) == 4
    assert {item["asset_id"] for item in launched["participants"]} == {
        "SEA-C2-01", "J16-01", "ATTACK-UAV-01", "ATTACK-UAV-02",
    }
    assert engine.waypoint_nav.get_route("J16-01")[-1]["label"] == "J16-RTB"
    assert engine.waypoint_nav.get_route("ATTACK-UAV-01")[-1]["label"] == "UAV01-RECOVERY"
    assert engine.waypoint_nav.get_route("ATTACK-UAV-02")[-1]["label"] == "UAV02-RECOVERY"
    assert engine.assets["J16-01"]["_current_behavior"] == "post_launch_egress"
    assert engine.assets["ATTACK-UAV-01"]["_current_behavior"] == "north_axis_egress"
    assert engine.assets["ATTACK-UAV-02"]["_current_behavior"] == "south_axis_egress"
    assert engine.assets["J16-01"]["speed_kts"] == 480
    assert engine.assets["ATTACK-UAV-01"]["speed_kts"] == 130
    assert engine.assets["ATTACK-UAV-02"]["speed_kts"] == 130
    assert any(
        item.get("type") == "post_launch_egress_started"
        and set(item.get("asset_ids") or []) == {"J16-01", "ATTACK-UAV-01", "ATTACK-UAV-02"}
        for item in engine.events
    )
    planned_arrivals = {
        item["planned_time_on_target_sec"] for item in engine.weapons.values()
    }
    assert len(planned_arrivals) == 1
    assert any(item["status"] == "scheduled" for item in engine.weapons.values())
    assert find_truth_leaks(launched) == []

    duplicate = engine.fire_weapon_at_track(
        track_id,
        asset_id="SEA-C2-01",
        weapon_name="舰载对陆巡航导弹",
        authorized=True,
    )
    assert "禁止重复发射" in duplicate["error"]

    operator = engine.get_operator_state()
    assert {item["coordination_role"] for item in operator["weapons"]} == {
        "maritime_strike_lead", "air_tactical_command_and_strike",
        "north_axis_strike", "south_axis_strike",
    }
    weapon_links = [
        item for item in operator["coordination_links"] if item["link_type"] == "weapon"
    ]
    assert len(weapon_links) == 4
    assert {item["status"] for item in weapon_links} == {"executing"}
    assert find_truth_leaks(operator) == []

    _advance(engine, 4200)
    assert engine.threats["COASTAL-SITE-01"]["neutralized"] is False
    assert not any(item.get("type") == "damage_assessment_confirmed" for item in engine.events)
    _advance(engine, 4510)
    assert engine.threats["COASTAL-SITE-01"]["neutralized"] is True
    hit_events = [item for item in engine.events if item.get("type") == "weapon_hit"]
    assert len(hit_events) == 4
    assert max(item["sim_time"] for item in hit_events) - min(
        item["sim_time"] for item in hit_events
    ) <= scenario["engagement_policy"]["coordinated_engagement"]["arrival_tolerance_sec"]
    tot_events = [
        item for item in engine.events if item.get("type") == "time_on_target_verified"
    ]
    assert len(tot_events) == 1
    assert tot_events[0]["actual_arrival_span_sec"] <= tot_events[0]["arrival_tolerance_sec"]
    damage_events = [
        item for item in engine.events if item.get("type") == "damage_assessment_confirmed"
    ]
    assert len(damage_events) == 1
    assert damage_events[0]["observer_asset_id"] == "WZ10-01"
    assert engine.waypoint_nav.get_route("WZ10-01")[-1]["label"] == "WZ10-RTB"
    assert engine.waypoint_nav.get_route("SEA-C2-01")[-1]["label"] == "C2-RECOVERY"
    assert engine.assets["WZ10-01"]["_current_behavior"] == "reconnaissance_return"
    assert engine.assets["SEA-C2-01"]["_current_behavior"] == "command_ship_recovery"
    assert any(
        item.get("type") == "post_bda_return_started"
        and set(item.get("asset_ids") or []) == {"WZ10-01", "SEA-C2-01"}
        for item in engine.events
    )
    assert "CJR-MEDIA-07" in {
        item["media_id"] for item in engine.media_capture.public_captures()
    }


def test_multimodal_evidence_releases_a_backend_classification_candidate() -> None:
    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)

    _advance(engine, 1530)
    assert all(
        track.classification == "UNKNOWN"
        for track in engine.sensor_fusion.tracks.values()
    )

    _advance(engine, 1710)
    candidates = list(engine.sensor_fusion.tracks.values())
    assert candidates
    assert all(track.classification == "COASTAL_MISSILE_SITE" for track in candidates)
    assert all(track.agent_assessment["status"] == "pending" for track in candidates)

    payload = CommanderBridge().build_workflow_payload(scenario, {}, engine)
    mission = payload["attachments"][0]["meta"]["amos_mission"]
    assert {contact["classification"] for contact in mission["contacts"]} == {
        "coastal_missile_site",
    }


def test_individual_assets_loiter_or_patrol_instead_of_freezing_on_station() -> None:
    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)

    _advance(engine, 3000)

    expected_behaviors = {
        "SEA-C2-01": "fire_area_patrol",
        "WZ10-01": "eo_elint_standoff_orbit",
        "J16-01": "standoff_strike_hold",
        "ATTACK-UAV-01": "north_release_station_orbit",
        "ATTACK-UAV-02": "south_release_station_orbit",
    }
    for asset_id, behavior in expected_behaviors.items():
        assert engine.assets[asset_id]["_current_behavior"] == behavior
        assert engine.assets[asset_id]["status"] == "active"
        assert float(engine.assets[asset_id]["speed_kts"]) > 0
        assert engine.waypoint_nav.get_route(asset_id)
        assert engine.waypoint_nav.get_mode(asset_id) == "loop"

    target = scenario["threats"][0]["position"]
    for asset_id in ("ATTACK-UAV-01", "ATTACK-UAV-02"):
        position = engine.assets[asset_id]["position"]
        assert distance_nm(
            position["lat"], position["lng"], target["lat"], target["lng"]
        ) <= 12


def test_airborne_resources_patrol_but_attack_uavs_launch_from_the_support_ship() -> None:
    scenario = get_scenario(SCENARIO_ID)
    assert scenario is not None
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)
    initial = {
        asset_id: dict(engine.assets[asset_id]["position"])
        for asset_id in engine.assets
    }
    operator_assets = {
        asset["id"]: asset for asset in engine.get_operator_state()["assets"]
    }
    assert operator_assets["WZ10-01"]["behavior_label"] == "侦察待命区标准盘旋"
    assert operator_assets["J16-01"]["behavior_label"] == "高空战斗空中巡逻待命"

    _advance(engine, 100)

    for asset_id in ("SEA-C2-01", "WZ10-01", "J16-01"):
        assert engine.assets[asset_id]["position"] != initial[asset_id]
    assert engine.assets["WZ10-01"]["_current_behavior"] == "airborne_standby_orbit"
    assert engine.assets["J16-01"]["_current_behavior"] == "combat_air_patrol"
    assert engine.assets["ATTACK-UAV-01"]["status"] == "staged"
    assert engine.assets["ATTACK-UAV-02"]["status"] == "staged"
    assert engine.assets["SAT-RECON-01"]["_current_behavior"] == "awaiting_orbital_access"
    assert engine.assets["SAT-RECON-02"]["_current_behavior"] == "awaiting_follow_on_access"

    _advance(engine, 2400)
    host = engine.assets["SEA-C2-01"]["position"]
    uav = engine.assets["ATTACK-UAV-01"]
    assert uav["status"] == "active"
    assert distance_nm(
        uav["_history_path"][0]["lat"], uav["_history_path"][0]["lng"],
        host["lat"], host["lng"],
    ) < 0.25


def test_protected_zone_buffer_blocks_the_individual_weapon_chain() -> None:
    scenario = deepcopy(get_scenario(SCENARIO_ID))
    assert scenario is not None
    target = scenario["threats"][0]["position"]
    scenario["protected_assets"][0].update({
        "lat": target["lat"],
        "lon": target["lng"],
    })
    engine = SimEngine(seed=int(scenario["default_seed"]))
    engine.load_scenario(scenario)
    engine.clock["run_id"] = "run-cjr-protected-test"
    _advance(engine, 3340)
    track_id = _apply_ground_site_assessment(engine, "wf-cjr-protected")
    eligibility = engine.engagement_eligibility(
        track_id,
        asset_id="SEA-C2-01",
        weapon_name="舰载对陆巡航导弹",
    )
    assert eligibility["eligible"] is False
    assert "保护区" in eligibility["reason"]

"""Scenario loading for the simulation engine."""

from __future__ import annotations

from typing import Any

from amos_platform.fusion.track_fusion import SensorFusionEngine
from amos_platform.simulation.world_state import MeshNetwork
from amos_platform.domain.policies.geofence import GeofenceManager
from amos_platform.frontend_state.temporal_story import build_internal_story


def load_scenario_into_engine(engine: Any, scenario: dict[str, Any], now_iso: Any) -> None:
    """Initialize engine state from an existing scenario dict."""
    engine.assets.clear()
    engine.threats.clear()
    engine.weapons.clear()
    engine.alerts.clear()
    engine.events.clear()
    engine.tasks.clear()
    engine._scenario_task_schedule = [
        dict(task) for task in scenario.get("asset_task_schedule") or []
        if isinstance(task, dict)
    ]
    engine._scenario_asset_follow_tasks = [
        dict(task) for task in scenario.get("asset_follow_tasks") or []
        if isinstance(task, dict)
    ]
    engine._scenario_capture_plans = [
        dict(capture) for capture in scenario.get("capture_plans") or []
        if isinstance(capture, dict)
    ]
    engine._engagement_policy = dict(scenario.get("engagement_policy") or {})
    engine._operator_contact_labels.clear()
    engine.exchange.reset()

    engine.sensor_fusion = SensorFusionEngine(rng=engine._rng)
    engine.waypoint_nav.clear_all()
    engine.mesh = MeshNetwork()
    engine.geofence = GeofenceManager()
    engine.clock["elapsed_sec"] = 0.0
    engine.clock["running"] = False
    engine.clock["lifecycle"] = "ready"
    engine.clock["started_at"] = None
    engine.clock["last_tick_wall_time"] = None
    engine.clock["last_tick_error"] = None
    for run_scoped_key in (
        "run_id",
        "scenario_id",
        "director_mode",
        "scenario_branch",
        "director_checkpoint_id",
        "roe_context",
    ):
        engine.clock.pop(run_scoped_key, None)
    engine.scenario_story = build_internal_story(scenario) if scenario.get("timeline") else {}
    engine._story_emitted = set()
    engine.media_capture.reset(
        scenario.get("capture_plans") or [],
        scenario.get("media_cues") or [],
        task_windows=scenario.get("asset_task_schedule") or [],
        asset_capabilities=scenario.get("asset_capabilities") or [],
        default_branch=str(scenario.get("default_branch") or "standard"),
    )

    scenario_routes = scenario.get("asset_routes") or {}
    route_modes = scenario.get("asset_route_modes") or {}
    motion_windows = scenario.get("asset_motion_windows") or {}
    follow_tasks_by_asset = {
        str(task.get("asset_id")): task
        for task in engine._scenario_asset_follow_tasks
        if task.get("asset_id")
    }
    observation_windows = scenario.get("threat_observation_windows") or {}
    raw_profiles = scenario.get("asset_profiles") or []
    if isinstance(raw_profiles, dict):
        asset_profiles = {
            str(asset_id): dict(profile)
            for asset_id, profile in raw_profiles.items()
            if isinstance(profile, dict)
        }
    else:
        asset_profiles = {
            str(profile.get("platform_id") or profile.get("asset_id") or profile.get("id")): dict(profile)
            for profile in raw_profiles
            if isinstance(profile, dict)
            and (profile.get("platform_id") or profile.get("asset_id") or profile.get("id"))
        }
    for raw in scenario.get("assets", []):
        pos = raw.get("position", {})
        aid = raw.get("asset_id", raw.get("id", ""))
        domain = raw.get("domain", "air")

        motion_window = motion_windows.get(aid) if isinstance(motion_windows.get(aid), dict) else {}
        follow_task = follow_tasks_by_asset.get(str(aid), {})
        cruise_speed = float(raw.get("speed_kts", 30) or 0)
        starts_at = float(motion_window.get("start_sec", 0) or 0)
        profile = asset_profiles.get(str(aid), {})
        motion_profile = profile.get("motion") if isinstance(profile.get("motion"), dict) else {}
        configured_status = str(raw.get("status", "operational"))
        default_turn_rate = 4.0 if domain == "air" else (0.8 if domain == "maritime" else 0.2)
        engine.assets[aid] = {
            "id": aid,
            "type": raw.get("type", raw.get("role", "unknown")),
            "domain": domain,
            "role": raw.get("role", ""),
            "position": {
                "lat": pos.get("lat", 0),
                "lng": pos.get("lng", 0),
                "alt_ft": pos.get("alt_ft", 0) if domain == "air" else 0,
            },
            "status": "staged" if starts_at > 0 and configured_status in {"active", "operational"} else configured_status,
            "_configured_status": configured_status,
            "heading_deg": raw.get("heading", raw.get("heading_deg", 0)),
            "speed_kts": 0.0 if starts_at > 0 else cruise_speed,
            "_cruise_speed_kts": cruise_speed,
            "_motion_window": dict(motion_window),
            "_operator_hidden_until_follow": bool(follow_task.get("hide_until_follow")),
            "max_turn_rate_dps": float(motion_profile.get("max_turn_rate_dps", default_turn_rate)),
            "sensors": raw.get("sensors", []),
            "weapons": raw.get("weapons", []),
            "autonomy_tier": raw.get("autonomy_tier", 2),
            "endurance_hr": raw.get("endurance_hr", 0),
            "health": raw.get("health", {
                "battery_pct": engine._rng.randint(85, 100),
                "comms_strength": engine._rng.randint(75, 100),
                "cpu_temp_c": engine._rng.randint(35, 55),
                "gps_fix": True,
            }),
            "_history_path": [{
                "lat": pos.get("lat", 0),
                "lng": pos.get("lng", 0),
                "sim_time": 0.0,
            }],
            "_profile": profile,
        }
        communications = profile.get("communications") if isinstance(profile, dict) else []
        comm_profile = communications[0] if isinstance(communications, list) and communications else None
        engine.mesh.register_node(
            aid,
            pos.get("lat", 0),
            pos.get("lng", 0),
            node_type="asset",
            comm_profile=comm_profile,
        )
        if aid in scenario_routes:
            engine.waypoint_nav.set_route(
                aid,
                scenario_routes[aid] or [],
                mode=str(route_modes.get(aid) or "hold"),
            )
        elif engine.assets[aid]["status"] in ("operational", "active"):
            engine._generate_patrol_route(aid)

    for raw in scenario.get("threats", []):
        pos = raw.get("position", {})
        tid = raw.get("threat_id", raw.get("id", ""))
        engine.threats[tid] = {
            "id": tid,
            "type": raw.get("threat_type", raw.get("type", "unknown")),
            "domain": raw.get("domain", "maritime"),
            "lat": pos.get("lat", 0),
            "lng": pos.get("lng", 0),
            "altitude_ft": pos.get("alt_ft", raw.get("altitude_ft", 0)),
            "heading": raw.get("heading", 0),
            "_commanded_heading": raw.get("heading", 0),
            "speed_kts": raw.get("speed_kts", 0),
            "risk_level": raw.get("risk_level", "MEDIUM"),
            "neutralized": False,
            "detected_by": [],
            "first_detected": None,
            "rf_freq_mhz": raw.get("rf_freq_mhz"),
            "power_dbm": raw.get("power_dbm"),
            "rcs_dbsm": raw.get("rcs_dbsm"),
            "ir_signature": raw.get("ir_signature", "medium"),
            "iff_status": raw.get("iff_status", "unknown"),
            "ais_match": raw.get("ais_match", False),
            "damage_state": raw.get("damage_state", "intact"),
            "hit_count": raw.get("hit_count", 0),
            "_behavior_script": raw.get("behavior_script"),
            "_behavior_phase": 0 if raw.get("behavior_script") else None,
            "_behavior_phase_name": (
                raw["behavior_script"]["phases"][0]["name"]
                if raw.get("behavior_script") and raw["behavior_script"].get("phases")
                else None
            ),
            "_phase_elapsed": 0.0,
            "_observation_window": dict(observation_windows.get(tid) or {}),
        }

    theater = scenario.get("theater", {})
    ao = theater.get("ao", {})
    if ao:
        engine.geofence.add(
            "Theater AO",
            [
                {"lat": ao.get("south", 10), "lng": ao.get("west", 110)},
                {"lat": ao.get("south", 10), "lng": ao.get("east", 118)},
                {"lat": ao.get("north", 20), "lng": ao.get("east", 118)},
                {"lat": ao.get("north", 20), "lng": ao.get("west", 110)},
            ],
            gtype="operational",
        )

    engine.alerts.append({
        "level": "INFO",
        "msg": f"场景资源就绪：{len(engine.assets)} 个己方平台",
        "time": now_iso(),
    })

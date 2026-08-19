"""Simulation state projection helpers."""

from __future__ import annotations

from typing import Any


def build_internal_state(engine: Any) -> dict:
    """Return current full simulation truth state for internal consumers."""
    retain_final_tracks = not bool(engine.clock.get("running")) and float(engine.clock.get("elapsed_sec", 0) or 0) > 0
    try:
        tracks = engine.sensor_fusion.get_tracks(include_stale=retain_final_tracks)
    except TypeError:  # Compatibility with lightweight adapters/test doubles.
        tracks = engine.sensor_fusion.get_tracks()
    coverage = engine.sensor_fusion.get_coverage()
    coverage_gaps = engine.sensor_fusion.get_coverage_gaps()
    try:
        kill_chain = engine.sensor_fusion.get_kill_chain_summary(include_stale=retain_final_tracks)
    except TypeError:
        kill_chain = engine.sensor_fusion.get_kill_chain_summary()
    topology = engine.mesh.get_topology()

    asset_list = []
    for aid, asset in engine.assets.items():
        pos = asset.get("position", asset)
        health = asset.get("health", {})
        route = engine.waypoint_nav.get_route(aid)
        asset_list.append({
            "id": aid,
            "type": asset.get("type", ""),
            "domain": asset.get("domain", ""),
            "role": asset.get("role", ""),
            "status": asset.get("status", ""),
            "position": {
                "lat": pos.get("lat", 0),
                "lng": pos.get("lng", 0),
                "alt_ft": pos.get("alt_ft", 0),
            },
            "heading": asset.get("heading_deg", 0),
            "speed_kts": asset.get("speed_kts", 0),
            "sensors": asset.get("sensors", []),
            "weapons": asset.get("weapons", []),
            "autonomy_tier": asset.get("autonomy_tier", 0),
            "endurance_hr": asset.get("endurance_hr", 0),
            "battery_pct": health.get("battery_pct", 100),
            "fuel_pct": health.get("fuel_pct"),
            "comms_strength": health.get("comms_strength", 100),
            "current_waypoint": route[0] if route else None,
            "waypoint_count": len(route),
            "history_path": list(asset.get("_history_path") or []),
            "capability_profile": dict(asset.get("_profile") or {}),
            "operator_hidden": bool(
                asset.get("_operator_hidden_until_follow")
                and not asset.get("_operator_follow_visible")
            ),
        })

    threat_list = []
    for tid, threat in engine.threats.items():
        fused = None
        for track in tracks.values():
            if track.get("associated_threat_id") == tid:
                fused = track
                break
        threat_list.append({
            "id": tid,
            "type": threat.get("type", ""),
            "domain": threat.get("domain", ""),
            "position": {"lat": threat.get("lat", 0), "lng": threat.get("lng", 0)},
            "altitude_ft": threat.get("altitude_ft", 0),
            "heading": threat.get("heading", 0),
            "speed_kts": threat.get("speed_kts", 0),
            "risk_level": threat.get("risk_level", "MEDIUM"),
            "neutralized": threat.get("neutralized", False),
            "detected": fused is not None,
            "fused_track_id": fused["id"] if fused else None,
            "kill_chain_phase": fused["kill_chain"]["phase"] if fused else None,
            "rcs_dbsm": threat.get("rcs_dbsm"),
            "ir_signature": threat.get("ir_signature", "medium"),
            "iff_status": threat.get("iff_status", "unknown"),
            "ais_match": threat.get("ais_match", False),
            "rf_freq_mhz": threat.get("rf_freq_mhz"),
            "power_dbm": threat.get("power_dbm"),
            "nearest_asset_id": threat.get("nearest_asset_id"),
            "nearest_asset_distance_nm": threat.get("nearest_asset_distance_nm"),
            "ttg_sec": threat.get("ttg_sec"),
            "damage_state": threat.get("damage_state", "intact"),
            "hit_count": threat.get("hit_count", 0),
            "behavior_phase": threat.get("_behavior_phase"),
            "behavior_phase_name": threat.get("_behavior_phase_name"),
            "parent_id": threat.get("parent_id"),
            "spawn_time": threat.get("spawn_time"),
            "injection_info": threat.get("injection_info"),
        })

    weapon_list = []
    for wid, weapon in engine.weapons.items():
        weapon_list.append({
            "id": wid,
            "type": weapon.get("type", ""),
            "weapon_type": weapon.get("weapon_type", ""),
            "category": weapon.get("category", ""),
            "domain": weapon.get("domain", "air"),
            "position": {"lat": weapon.get("lat", 0), "lng": weapon.get("lng", 0)},
            "heading": weapon.get("heading", 0),
            "speed_kts": weapon.get("speed_kts", 0),
            "source_asset_id": weapon.get("source_asset_id"),
            "target_threat_id": weapon.get("target_threat_id"),
            "target_track_id": weapon.get("target_track_id"),
            "launch_sim_time": weapon.get("launch_time"),
            "eta_sec": weapon.get("eta_sec"),
            "p_kill": weapon.get("p_kill"),
            "status": weapon.get("status", "unknown"),
            "damage_state": weapon.get("damage_state"),
            "impact_sim_time": weapon.get("impact_sim_time"),
            "assessed_sim_time": weapon.get("assessed_sim_time"),
        })

    return {
        "visibility": "truth/internal",
        "clock": dict(engine.clock),
        "assets": asset_list,
        "threats": threat_list,
        "weapons": weapon_list,
        "observation_batch": dict(getattr(engine.sensor_fusion, "last_observation_batch", {}) or {}),
        "fused_tracks": list(tracks.values()),
        "kill_chain": kill_chain,
        "scenario_story": dict(getattr(engine, "scenario_story", {}) or {}),
        "coverage": {
            "footprints": coverage,
            "gaps": coverage_gaps,
            "gap_count": len(coverage_gaps),
        },
        "network": topology,
        "alerts": engine.alerts[-20:],
        "tasks": engine.tasks[-10:],
        "kill_chain_events": list(engine.events[-50:]),
        "stats": {
            "asset_count": len(engine.assets),
            "threat_count": len(engine.threats),
            "weapon_count": len(engine.weapons),
            "track_count": len(tracks),
            "event_count": len(engine.events),
            "alert_count": len(engine.alerts),
        },
    }


__all__ = ["build_internal_state"]

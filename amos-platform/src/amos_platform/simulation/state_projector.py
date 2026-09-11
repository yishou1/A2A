"""Simulation state projection helpers."""

from __future__ import annotations

from typing import Any


def _coordination_links(engine: Any, tracks: dict[str, dict[str, Any]], topology: dict[str, Any]) -> list[dict[str, Any]]:
    """Project only currently active, operator-safe coordination endpoints."""
    elapsed = float(engine.clock.get("elapsed_sec", 0) or 0)
    events = [item for item in engine.events if isinstance(item, dict)]
    has_authorized_fire = any(item.get("type") == "authorized_fire_command" for item in events)
    has_impact = any(item.get("type") == "weapon_hit" for item in events)
    has_assessment = any(item.get("type") == "damage_assessment_confirmed" for item in events)
    network_quality = {
        frozenset((str(item.get("from") or ""), str(item.get("to") or ""))): float(item.get("quality", 0) or 0)
        for item in topology.get("link_records") or []
        if isinstance(item, dict)
    }
    result: list[dict[str, Any]] = []
    for raw in getattr(engine, "_scenario_coordination_links", []) or []:
        if elapsed < float(raw.get("active_from_sec", 0) or 0):
            continue
        source_id = str(raw.get("source_asset_id") or "")
        source = engine.assets.get(source_id)
        if not source:
            continue
        target_id = str(raw.get("target_asset_id") or "")
        target_asset = engine.assets.get(target_id) if target_id else None
        target_track = None
        if not target_asset and raw.get("target_ref"):
            target_track = next(
                (
                    track for track_id, track in tracks.items()
                    if str(engine._truth_target_for_track(
                        engine.sensor_fusion.tracks.get(str(track_id))
                    ) or "") == str(raw.get("target_ref"))
                ),
                None,
            )
            if target_track is None:
                continue
        source_position = source.get("position") or source
        if target_asset:
            target_position = target_asset.get("position") or target_asset
        else:
            target_position = target_track or {}
        link_type = str(raw.get("link_type") or "coordination")
        if link_type == "weapon":
            status = "complete" if has_assessment else (
                "effect_pending" if has_impact else ("executing" if has_authorized_fire else "ready")
            )
        else:
            endpoint_statuses = {
                str(source.get("status") or "").casefold(),
                str((target_asset or {}).get("status") or "").casefold(),
            }
            quality = network_quality.get(frozenset((source_id, target_id))) if target_id else None
            status = "degraded" if endpoint_statuses.intersection({"degraded", "unavailable"}) or (
                quality is not None and quality <= 0.3
            ) else "active"
        result.append({
            "link_id": str(raw.get("link_id") or ""),
            "link_type": link_type,
            "label": str(raw.get("label") or link_type),
            "status": status,
            "source_asset_id": source_id,
            "target_asset_id": target_id or None,
            "target_track_id": str(target_track.get("id") or target_track.get("track_id") or "") if target_track else None,
            "source_position": {"lat": source_position.get("lat", 0), "lng": source_position.get("lng", source_position.get("lon", 0))},
            "target_position": {"lat": target_position.get("lat", 0), "lng": target_position.get("lng", target_position.get("lon", 0))},
        })
    return result


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
    coordination_links = _coordination_links(engine, tracks, topology)

    asset_list = []
    elapsed = float(engine.clock.get("elapsed_sec", 0) or 0)
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
            "behavior": asset.get("_current_behavior", ""),
            "behavior_label": asset.get("_current_behavior_label", ""),
            "sensors": asset.get("sensors", []),
            "weapons": asset.get("weapons", []),
            "autonomy_tier": asset.get("autonomy_tier", 0),
            "endurance_hr": asset.get("endurance_hr", 0),
            "member_count": int(asset.get("member_count", 1) or 1),
            "formation_role": asset.get("formation_role", ""),
            "network_role": asset.get("network_role", ""),
            "battery_pct": health.get("battery_pct"),
            "fuel_pct": health.get("fuel_pct"),
            "comms_strength": health.get("comms_strength"),
            "current_waypoint": route[0] if route else None,
            "waypoint_count": len(route),
            "history_path": list(asset.get("_history_path") or []),
            "capability_profile": dict(asset.get("_profile") or {}),
            "operator_hidden": bool(
                (
                    asset.get("_operator_hidden_until_follow")
                    and not asset.get("_operator_follow_visible")
                )
                or elapsed < float(asset.get("_operator_visible_from_sec", 0) or 0)
                or (
                    asset.get("_operator_visible_until_sec") is not None
                    and elapsed >= float(asset["_operator_visible_until_sec"])
                )
                or asset.get("_operator_hidden_after_launch")
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
            "coordination_chain_id": weapon.get("coordination_chain_id"),
            "coordination_role": weapon.get("coordination_role"),
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
        "coordination_links": coordination_links,
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

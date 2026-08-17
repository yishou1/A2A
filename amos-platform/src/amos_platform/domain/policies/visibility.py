"""Visibility boundary helpers for truth, agent, and operator payloads."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


FORBIDDEN_TRUTH_KEYS = {
    "truth_id",
    "threat_id",
    "threat_type",
    "risk_level",
    "threat_level",
    "is_hostile",
    "hostility",
    "enemy_type",
    "side",
    "iff_status",
    "ais_match",
    "associated_threat_id",
    "target_threat_id",
    "ground_truth_threat_level",
    "truth_bbox",
    "true_label",
    "truth_label",
    "truth_track",
    "truth_associations",
    "kill_chain",
    "p_kill",
}

MEDIA_FORBIDDEN_TRUTH_KEYS = FORBIDDEN_TRUTH_KEYS | {
    "bbox_truth",
    "label_truth",
}


def _position(entity: dict[str, Any]) -> dict[str, Any]:
    pos = entity.get("position") if isinstance(entity.get("position"), dict) else entity
    return {
        "lat": pos.get("lat", 0),
        "lng": pos.get("lng", pos.get("lon", 0)),
        "alt_ft": pos.get("alt_ft", 0),
    }


def truth_terms_from_state(state: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for threat in state.get("threats") or []:
        for key in ("id", "type"):
            value = threat.get(key)
            if value:
                terms.append(str(value))
    return sorted(set(terms), key=len, reverse=True)


def _redact_string(value: str, truth_terms: list[str]) -> str:
    redacted = value
    for term in truth_terms:
        redacted = redacted.replace(term, "待识别接触")
    return redacted


def remove_truth_fields(value: Any, truth_terms: list[str] | None = None) -> Any:
    truth_terms = truth_terms or []
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            if key in FORBIDDEN_TRUTH_KEYS:
                continue
            clean[key] = remove_truth_fields(item, truth_terms)
        return clean
    if isinstance(value, list):
        return [remove_truth_fields(item, truth_terms) for item in value]
    if isinstance(value, str):
        return _redact_string(value, truth_terms)
    return value


def _truth_terms_for_payload(source_state: dict[str, Any] | None) -> list[str]:
    if not source_state:
        return []
    return truth_terms_from_state(source_state)


def find_truth_leaks(
    value: Any,
    truth_terms: list[str] | None = None,
    path: str = "$",
    context: str = "",
    forbidden_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return structured leak records for forbidden truth keys or raw terms."""
    truth_terms = truth_terms or []
    forbidden_keys = forbidden_keys or FORBIDDEN_TRUTH_KEYS
    leaks: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if key in forbidden_keys:
                leaks.append({
                    "path": child_path,
                    "key": key,
                    "value": item,
                    "reason": "forbidden_truth_key",
                    "context": context,
                })
            leaks.extend(find_truth_leaks(
                item,
                truth_terms,
                child_path,
                context,
                forbidden_keys,
            ))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            leaks.extend(find_truth_leaks(
                item,
                truth_terms,
                f"{path}[{index}]",
                context,
                forbidden_keys,
            ))
    elif isinstance(value, str):
        for term in truth_terms:
            if term and term in value:
                leaks.append({
                    "path": path,
                    "key": None,
                    "value": value,
                    "reason": "raw_truth_term",
                    "context": context,
                })
                break
    return leaks


def assert_no_truth_leak(
    payload: Any,
    *,
    truth_terms: list[str] | None = None,
    context: str = "",
    forbidden_keys: set[str] | None = None,
) -> None:
    leaks = find_truth_leaks(
        payload,
        truth_terms=truth_terms,
        context=context,
        forbidden_keys=forbidden_keys,
    )
    if not leaks:
        return
    paths = ", ".join(str(leak["path"]) for leak in leaks[:5])
    suffix = "" if len(leaks) <= 5 else f", ... +{len(leaks) - 5} more"
    label = f"{context}: " if context else ""
    raise ValueError(f"{label}truth leak detected at {paths}{suffix}")


def sanitize_operator_payload(payload: Any, *, source_state: dict[str, Any] | None = None) -> Any:
    truth_terms = _truth_terms_for_payload(source_state)
    safe = remove_truth_fields(payload, truth_terms)
    assert_no_truth_leak(safe, truth_terms=truth_terms, context="operator")
    return safe


def sanitize_agent_payload(payload: Any, *, source_state: dict[str, Any] | None = None) -> Any:
    truth_terms = _truth_terms_for_payload(source_state)
    safe = remove_truth_fields(payload, truth_terms)
    assert_no_truth_leak(safe, truth_terms=truth_terms, context="agent")
    return safe


def sanitize_media_payload(payload: Any, *, source_state: dict[str, Any] | None = None) -> Any:
    truth_terms = _truth_terms_for_payload(source_state)
    safe = remove_truth_fields(payload, truth_terms)
    assert_no_truth_leak(
        safe,
        truth_terms=truth_terms,
        context="media",
        forbidden_keys=MEDIA_FORBIDDEN_TRUTH_KEYS,
    )
    return safe


def sanitize_asset(asset: dict[str, Any]) -> dict[str, Any]:
    asset_id = asset.get("id") or asset.get("asset_id") or ""
    return {
        "id": asset_id,
        "type": asset.get("type", ""),
        "domain": asset.get("domain", ""),
        "role": asset.get("role", ""),
        "status": asset.get("status", ""),
        "position": _position(asset),
        "heading": asset.get("heading", asset.get("heading_deg", 0)),
        "speed_kts": asset.get("speed_kts", 0),
        "sensors": list(asset.get("sensors") or []),
        "weapons": list(asset.get("weapons") or []),
        "autonomy_tier": asset.get("autonomy_tier"),
        "endurance_hr": asset.get("endurance_hr"),
        "battery_pct": asset.get("battery_pct"),
        "fuel_pct": asset.get("fuel_pct"),
        "comms_strength": asset.get("comms_strength"),
        "history_path": deepcopy(asset.get("history_path") or []),
        "capability_profile": deepcopy(asset.get("capability_profile") or {}),
    }


def sanitize_fused_track(track: dict[str, Any]) -> dict[str, Any]:
    assessment = deepcopy(track.get("agent_assessment") or {
        "status": "pending",
        "label": "待后端识别",
        "source": None,
    })
    kill_chain = track.get("kill_chain") if isinstance(track.get("kill_chain"), dict) else {}
    safe = {
        "id": track.get("id") or track.get("track_id"),
        "track_id": track.get("track_id") or track.get("id"),
        "lat": track.get("lat"),
        "lng": track.get("lng"),
        "confidence": track.get("confidence"),
        "classification": track.get("classification", "UNKNOWN"),
        "domain_hint": track.get("domain_hint"),
        "heading": track.get("heading"),
        "history_path": deepcopy(track.get("history_path") or []),
        "agent_assessment": assessment,
        # These scalar fields are safe operator projections.  The raw
        # ``threat_level`` and ``kill_chain`` structures remain forbidden.
        "assessment_level": (
            assessment.get("level") or track.get("threat_level")
        ) if assessment.get("source") else None,
        "kill_chain_phase": kill_chain.get("phase"),
        "source_count": track.get("source_count"),
        "sources": list(track.get("sources") or []),
        "source_refs": deepcopy(track.get("source_refs") or []),
        "velocity": deepcopy(track.get("velocity") or {}),
        "uncertainty": deepcopy(track.get("uncertainty") or {}),
        "kill_chain": deepcopy(track.get("kill_chain") or {}),
        "age_sec": track.get("age_sec"),
        "visibility": "operator-visible:fused_track",
    }
    return {key: value for key, value in safe.items() if value is not None}


def observation_from_track(track: dict[str, Any]) -> dict[str, Any] | None:
    lat = track.get("lat")
    lng = track.get("lng")
    if lat is None or lng is None:
        return None
    track_id = track.get("track_id") or track.get("id") or "track"
    return {
        "observation_id": f"OBS-{track_id}",
        "observation_type": "radar_detection",
        "track_id": track_id,
        "position": {"lat": lat, "lng": lng},
        "confidence": track.get("confidence"),
        "sensor_sources": list(track.get("sources") or []),
        "metadata": {
            "visibility": "agent-visible:observation",
            "source": "sensor_fusion",
        },
    }


def sanitize_coverage(coverage: dict[str, Any]) -> dict[str, Any]:
    return {
        "footprints": deepcopy(coverage.get("footprints") or {}),
        "gaps": [],
        "gap_count": 0,
        "visibility": "operator-visible",
    }


def sanitize_weapon(weapon: dict[str, Any], truth_terms: list[str]) -> dict[str, Any]:
    safe = remove_truth_fields(weapon, truth_terms)
    safe.pop("p_kill", None)
    return safe


def sanitize_stats(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset_count": stats.get("asset_count", 0),
        "weapon_count": stats.get("weapon_count", 0),
        "track_count": stats.get("track_count", 0),
        "event_count": stats.get("event_count", 0),
        "alert_count": stats.get("alert_count", 0),
    }


def build_operator_state(internal_state: dict[str, Any]) -> dict[str, Any]:
    truth_terms = truth_terms_from_state(internal_state)
    tracks = []
    for index, track in enumerate(internal_state.get("fused_tracks") or [], start=1):
        safe_track = sanitize_fused_track(track)
        safe_track["display_label"] = f"海面接触 {index:02d}"
        tracks.append(safe_track)
    return sanitize_operator_payload({
        "visibility": "operator-visible",
        "clock": deepcopy(internal_state.get("clock") or {}),
        "assets": [sanitize_asset(asset) for asset in internal_state.get("assets") or []],
        "weapons": [
            sanitize_weapon(weapon, truth_terms)
            for weapon in internal_state.get("weapons") or []
        ],
        "fused_tracks": tracks,
        "kill_chain": deepcopy(internal_state.get("kill_chain") or {}),
        "coverage": sanitize_coverage(internal_state.get("coverage") or {}),
        "network": remove_truth_fields(internal_state.get("network") or {}, truth_terms),
        "alerts": remove_truth_fields(internal_state.get("alerts") or [], truth_terms),
        "tasks": remove_truth_fields(internal_state.get("tasks") or [], truth_terms),
        "stats": sanitize_stats(internal_state.get("stats") or {}),
    }, source_state=internal_state)


def build_agent_visible_state(internal_state: dict[str, Any]) -> dict[str, Any]:
    operator_state = build_operator_state(internal_state)
    observations = []
    for track in operator_state.get("fused_tracks") or []:
        observation = observation_from_track(track)
        if observation:
            observations.append(observation)
    return sanitize_agent_payload({
        "visibility": "agent-visible",
        "clock": operator_state.get("clock", {}),
        "own_asset_poses": operator_state.get("assets", []),
        "observations": observations,
        "coverage": operator_state.get("coverage", {}),
        "network": operator_state.get("network", {}),
        "alerts": operator_state.get("alerts", []),
    }, source_state=internal_state)

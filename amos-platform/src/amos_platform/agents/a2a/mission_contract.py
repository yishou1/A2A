"""Causal AMOS mission data mapped to the integrated A2A input shape.

This module contains only presentation-safe simulation data.  It deliberately
does not infer affiliation, intent, or threat level; those remain backend
outputs.
"""

from __future__ import annotations

import math
from collections import defaultdict
from copy import deepcopy
from typing import Any


MAX_PERCEPTION_FRAMES = 10


def _clamp(value: Any, lower: float = 0.0, upper: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = lower
    return max(lower, min(upper, number))


def _position(value: dict[str, Any]) -> dict[str, float]:
    raw = value.get("position") if isinstance(value.get("position"), dict) else value
    return {
        "lat": round(float(raw.get("lat", 0.0) or 0.0), 6),
        "lon": round(float(raw.get("lng", raw.get("lon", 0.0)) or 0.0), 6),
        "alt_ft": round(float(raw.get("alt_ft", raw.get("altitude_ft", 0.0)) or 0.0), 1),
    }


def _domain_object_type(domain: Any) -> str:
    normalized = str(domain or "").casefold()
    if normalized in {"air", "airborne", "aviation"}:
        return "aircraft"
    if normalized in {"maritime", "surface", "sea"}:
        return "ship"
    return "unknown"


def _contact_kind(domain: Any) -> str:
    normalized = str(domain or "").casefold()
    if normalized == "air":
        return "air-contact"
    if normalized in {"maritime", "surface", "sea"}:
        return "surface-contact"
    return "unknown-contact"


def _sensor_token(value: Any) -> str:
    return "".join(character for character in str(value or "").casefold() if character.isalnum())


def _speed_heading(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    fallback_heading: float,
) -> tuple[float, float]:
    if not previous:
        return 0.0, fallback_heading % 360.0
    dt = float(current.get("sim_time", 0.0)) - float(previous.get("sim_time", 0.0))
    if dt <= 0:
        return 0.0, fallback_heading % 360.0
    mean_lat = math.radians((float(previous["lat"]) + float(current["lat"])) / 2.0)
    north_m = (float(current["lat"]) - float(previous["lat"])) * 111_320.0
    east_m = (
        (float(current["lng"]) - float(previous["lng"]))
        * 111_320.0
        * max(0.01, math.cos(mean_lat))
    )
    distance_m = math.hypot(north_m, east_m)
    if distance_m < 0.5:
        return 0.0, fallback_heading % 360.0
    heading = (math.degrees(math.atan2(east_m, north_m)) + 360.0) % 360.0
    return round(distance_m / dt, 3), round(heading, 2)


def map_observations(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preserve sensor measurements without promoting them to independent tracks."""
    mapped = []
    for item in observations:
        if not isinstance(item, dict) or not item.get("observation_id"):
            continue
        mapped.append({
            "observation_id": str(item["observation_id"]),
            "sensor_id": item.get("sensor_id"),
            "platform_id": item.get("asset_id"),
            "sim_time": float(item.get("sim_time", 0.0) or 0.0),
            "modality": item.get("modality"),
            "domain_hint": item.get("domain_hint"),
            "bearing_deg": item.get("bearing_deg"),
            "range_nm": item.get("range_nm"),
            "slant_range_nm": item.get("slant_range_nm"),
            "elevation_deg": item.get("elevation_deg"),
            "range_rate_kts": item.get("range_rate_kts"),
            "snr_db": item.get("snr_db"),
            "frequency_mhz": item.get("frequency_mhz"),
            "power_dbm": item.get("power_dbm"),
            "covariance": deepcopy(item.get("covariance") or {}),
            "confidence": item.get("confidence"),
            "quality": item.get("quality"),
            "source": item.get("source") or "sensor_simulator",
            "capture_ids": [str(value) for value in item.get("capture_ids") or [] if value],
            "media_ids": [str(value) for value in item.get("media_ids") or [] if value],
        })
    return mapped


def map_friendly_platforms(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map AMOS assets to IntegratedMissionRequest.FriendlyPlatform."""
    platforms = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        platform_id = str(asset.get("id") or asset.get("asset_id") or "").strip()
        if not platform_id:
            continue
        position = _position(asset)
        energy_value = asset.get("battery_pct")
        if energy_value is None:
            energy_value = asset.get("fuel_pct", 100)
        battery = _clamp(energy_value, 0.0, 100.0) / 100.0
        comms = _clamp(asset.get("comms_strength", 100), 0.0, 100.0) / 100.0
        active = str(asset.get("status") or "").casefold() in {"active", "operational", "holding"}
        readiness = round((battery * 0.55 + comms * 0.45) if active else 0.0, 3)
        weapons = list(asset.get("weapons") or [])
        platforms.append({
            "platform_id": platform_id,
            "platform_type": asset.get("type") or asset.get("role") or asset.get("domain") or "generic",
            "readiness": readiness,
            # The scenario has weapon-system declarations, not an inventory.
            # Keep the value deterministic and explain its basis in metadata.
            "munitions": len(weapons),
            "location": f"{position['lat']},{position['lon']}",
            "metadata": {
                "position": position,
                "domain": asset.get("domain"),
                "role": asset.get("role"),
                "status": asset.get("status"),
                "heading_deg": asset.get("heading", asset.get("heading_deg", 0)),
                "speed_kts": asset.get("speed_kts", 0),
                "sensors": list(asset.get("sensors") or []),
                "weapons": weapons,
                "battery_pct": asset.get("battery_pct"),
                "fuel_pct": asset.get("fuel_pct"),
                "comms_strength": asset.get("comms_strength"),
                "capability_profile": deepcopy(asset.get("capability_profile") or {}),
                "munition_basis": "configured_weapon_system_count",
            },
        })
    return platforms


def map_fused_contacts(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use stable fused-track IDs as contacts while leaving risk unknown."""
    contacts = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        track_id = str(track.get("track_id") or track.get("id") or "").strip()
        lat = track.get("lat")
        lon = track.get("lng", track.get("lon"))
        if not track_id or lat is None or lon is None:
            continue
        geo = {"lat": round(float(lat), 6), "lon": round(float(lon), 6)}
        source_refs = [
            deepcopy(ref) for ref in track.get("source_refs") or []
            if isinstance(ref, dict)
        ]
        contacts.append({
            "contact_id": track_id,
            "kind": _contact_kind(track.get("domain_hint")),
            "location": f"{geo['lat']},{geo['lon']}",
            "velocity": None,
            "intent": None,
            "metadata": {
                "amos_track_id": track_id,
                "geo": geo,
                "domain_hint": track.get("domain_hint"),
                "classification": "unknown",
                "affiliation": "unknown",
                "confidence": track.get("confidence"),
                "heading_deg": track.get("heading"),
                "uncertainty": deepcopy(track.get("uncertainty") or {}),
                "source_observation_ids": [
                    str(ref["observation_id"])
                    for ref in source_refs
                    if ref.get("observation_id")
                ],
                "sensor_sources": list(track.get("sources") or []),
            },
            # Compatibility fields retained for the current Commander branch.
            "track_id": track_id,
            "geo": geo,
            "classification": "unknown",
            "affiliation": "unknown",
        })
    return contacts


def build_perception_frames(
    tracks: list[dict[str, Any]],
    *,
    scenario_id: str,
    protected_assets: list[dict[str, Any]],
    cutoff_sec: float,
    max_frames: int = MAX_PERCEPTION_FRAMES,
) -> list[dict[str, Any]]:
    """Build a causal, stable-ID time series from fused-track histories."""
    detections_by_time: dict[float, list[dict[str, Any]]] = defaultdict(list)
    for track in tracks:
        if not isinstance(track, dict):
            continue
        track_id = str(track.get("track_id") or track.get("id") or "").strip()
        if not track_id:
            continue
        history = [
            point for point in track.get("history_path") or []
            if isinstance(point, dict)
            and point.get("lat") is not None
            and point.get("lng", point.get("lon")) is not None
            and float(point.get("sim_time", 0.0) or 0.0) <= cutoff_sec
        ]
        history.sort(key=lambda point: float(point.get("sim_time", 0.0) or 0.0))
        previous = None
        fallback_heading = float(track.get("heading", 0.0) or 0.0)
        for index, point in enumerate(history):
            normalized = {
                "lat": float(point["lat"]),
                "lng": float(point.get("lng", point.get("lon"))),
                "sim_time": round(float(point.get("sim_time", 0.0) or 0.0), 3),
            }
            point_heading = float(point.get("heading", fallback_heading) or fallback_heading)
            speed, heading = _speed_heading(previous, normalized, point_heading)
            frame_time = normalized["sim_time"]
            source_observation_ids = [
                str(value) for value in point.get("source_observation_ids") or [] if value
            ]
            detections_by_time[frame_time].append({
                "detection_id": f"{track_id}-t{int(round(frame_time * 1000)):09d}",
                "object_type": _domain_object_type(track.get("domain_hint")),
                "timestamp": frame_time,
                "lat": round(normalized["lat"], 6),
                "lon": round(normalized["lng"], 6),
                "alt": float(point.get("alt_m", point.get("alt", 0.0)) or 0.0),
                "speed": speed,
                "heading": heading,
                "confidence": _clamp(point.get("confidence", track.get("confidence", 0.5))),
                "source_agent": "amos_sensor_fusion",
                "metadata": {
                    "amos_track_id": track_id,
                    "source_contact_id": track_id,
                    "domain_hint": track.get("domain_hint"),
                    "sample_index": index,
                    "uncertainty": {
                        "semi_major_deg": point.get("uncertainty_semi_major_deg"),
                        "semi_minor_deg": point.get("uncertainty_semi_minor_deg"),
                        "angle_deg": point.get("uncertainty_angle_deg"),
                    },
                    "source_observation_ids": source_observation_ids,
                    "source_media_ids": [
                        str(value) for value in point.get("source_media_ids") or [] if value
                    ],
                    "source_capture_ids": [
                        str(value) for value in point.get("source_capture_ids") or [] if value
                    ],
                },
            })
            previous = normalized

    frame_times = sorted(detections_by_time)[-max(1, int(max_frames)):]
    scene = {
        "scenario_id": scenario_id,
        "protected_assets": deepcopy(protected_assets),
    }
    return [
        {
            "task_id": f"{scenario_id}-frame-{int(round(frame_time * 1000)):09d}",
            "message_type": "perception_result",
            "algorithm_level": "medium",
            "scene": deepcopy(scene),
            "detections": detections_by_time[frame_time],
        }
        for frame_time in frame_times
    ]


def link_evidence(
    attachments: list[dict[str, Any]],
    tracks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Link each released media item to compatible causal sensor references."""
    rows = []
    for item in attachments:
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        sensor_id = str(meta.get("sensor_id") or "")
        platform_id, _, sensor_name = sensor_id.partition("/")
        media_sensor_token = _sensor_token(sensor_name)
        observation_ids: list[str] = [
            str(value) for value in meta.get("observation_ids") or [] if value
        ]
        track_ids: list[str] = [
            str(value) for value in meta.get("track_ids") or [] if value
        ]
        for track in tracks:
            track_id = str(track.get("track_id") or track.get("id") or "")
            matched = False
            for ref in track.get("source_refs") or []:
                if not isinstance(ref, dict):
                    continue
                ref_sensor = str(ref.get("sensor_id") or "")
                ref_platform = str(ref.get("asset_id") or "")
                ref_sensor_token = _sensor_token(ref_sensor)
                sensor_match = (
                    not media_sensor_token
                    or not ref_sensor_token
                    or media_sensor_token in ref_sensor_token
                    or ref_sensor_token in media_sensor_token
                )
                platform_match = not platform_id or platform_id == ref_platform
                if sensor_match and platform_match:
                    matched = True
                    if ref.get("observation_id"):
                        observation_ids.append(str(ref["observation_id"]))
            if matched and track_id:
                track_ids.append(track_id)
        rows.append({
            "media_id": item.get("id"),
            "modality": meta.get("modality"),
            "description": meta.get("text"),
            "captured_at_sim_time": meta.get("captured_at_sim_time"),
            "sensor_id": meta.get("sensor_id"),
            "capture_id": meta.get("capture_id"),
            "product_type": meta.get("product_type"),
            "track_ids": sorted(set(track_ids)),
            "observation_ids": sorted(set(observation_ids)),
            "source_capture_ids": list(meta.get("source_capture_ids") or []),
            "capture_provenance": deepcopy(meta.get("capture_provenance") or {}),
            "checksum": deepcopy(item.get("checksum") or {}),
        })
    return rows


__all__ = [
    "MAX_PERCEPTION_FRAMES",
    "build_perception_frames",
    "link_evidence",
    "map_friendly_platforms",
    "map_fused_contacts",
    "map_observations",
]

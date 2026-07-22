"""Helpers for building deterministic demo scenarios.

The scenarios are fictional joint situation-awareness exercises. They are
designed to look operationally plausible while staying strictly simulation-only:
tracking, prediction, grouping, and attention-priority ranking only.
"""

from __future__ import annotations

import math
from typing import Dict, List

from .utils import project_position, speed_heading_to_velocity


def default_protected_assets() -> List[Dict[str, object]]:
    return [
        {
            "asset_id": "blue-c2-node",
            "asset_name": "Blue C2 Node",
            "asset_type": "command_post",
            "lat": 31.2304,
            "lon": 121.4737,
            "alt": 30,
            "protection_radius_m": 9_000,
            "criticality": 0.95,
            "status": "protected",
            "metadata": {"role": "primary command and coordination node", "side": "blue"},
        },
        {
            "asset_id": "blue-coastal-radar",
            "asset_name": "Blue Coastal Radar Site",
            "asset_type": "radar_site",
            "lat": 31.2850,
            "lon": 121.4380,
            "alt": 45,
            "protection_radius_m": 7_500,
            "criticality": 0.88,
            "status": "protected",
            "metadata": {"role": "early warning and tracking sensor", "side": "blue"},
        },
        {
            "asset_id": "blue-logistics-pier",
            "asset_name": "Blue Logistics Pier",
            "asset_type": "logistics_node",
            "lat": 31.2440,
            "lon": 121.5350,
            "alt": 8,
            "protection_radius_m": 6_500,
            "criticality": 0.82,
            "status": "protected",
            "metadata": {"role": "supply staging and sustainment node", "side": "blue"},
        },
        {
            "asset_id": "blue-medical-area",
            "asset_name": "Blue Medical Assembly Area",
            "asset_type": "medical_node",
            "lat": 31.2050,
            "lon": 121.4450,
            "alt": 20,
            "protection_radius_m": 5_500,
            "criticality": 0.74,
            "status": "protected",
            "metadata": {"role": "casualty collection and evacuation support", "side": "blue"},
        },
    ]


def default_scene() -> Dict[str, object]:
    return {
        "protected_zone_lat": 31.2304,
        "protected_zone_lon": 121.4737,
        "protected_radius_m": 30_000.0,
        "operation_name": "fictional_littoral_protection_exercise",
        "operation_phase": "generated_per_frame",
        "protected_assets": default_protected_assets(),
    }


def wrap_payload(task_id: str, detections: List[Dict[str, object]], algorithm_level: str = "medium") -> Dict[str, object]:
    return {
        "task_id": task_id,
        "message_type": "perception_result",
        "algorithm_level": algorithm_level,
        "scene": default_scene(),
        "detections": detections,
    }


def generate_auto_demo_frame(frame_index: int) -> Dict[str, object]:
    """Generate one deterministic frame for the AMOS C2 map demo.

    The fictional exercise contains a coastal protected zone, protected blue
    assets, a three-aircraft patrol formation, a two-ship surface group, one
    low-altitude UAV, and an intermittent unknown track. The 90-frame sequence
    moves through detection, tracking, formation recognition, protected-asset
    impact analysis, and anomaly escalation without implying any engagement
    recommendation.
    """

    timestamp = 3_000.0 + frame_index
    phase = _operation_phase(frame_index)
    specs = [
        {
            "id": "auto-air-patrol-1",
            "object_type": "aircraft",
            "lat": 31.3900,
            "lon": 121.3200,
            "alt": 7800,
            "speed": 218,
            "heading": 134,
            "confidence": 0.94,
            "source_agent": "coastal-air-radar",
            "metadata": {
                "scenario_role": "air_patrol_lead",
                "sensor_mode": "track-while-scan",
                "exercise_area": "fictional_littoral_security_zone",
                "operation_phase": phase,
            },
        },
        {
            "id": "auto-air-patrol-2",
            "object_type": "aircraft",
            "lat": 31.3980,
            "lon": 121.3330,
            "alt": 7720,
            "speed": 216,
            "heading": 136,
            "confidence": 0.91,
            "source_agent": "coastal-air-radar",
            "metadata": {
                "scenario_role": "air_patrol_wing",
                "sensor_mode": "track-while-scan",
                "formation_hint": "loose_wedge",
                "operation_phase": phase,
            },
        },
        {
            "id": "auto-air-patrol-3",
            "object_type": "aircraft",
            "lat": 31.3820,
            "lon": 121.3070,
            "alt": 7880,
            "speed": 221,
            "heading": 133,
            "confidence": 0.90,
            "source_agent": "coastal-air-radar",
            "metadata": {
                "scenario_role": "air_patrol_wing",
                "sensor_mode": "track-while-scan",
                "formation_hint": "loose_wedge",
                "operation_phase": phase,
            },
        },
        {
            "id": "auto-surface-1",
            "object_type": "ship",
            "lat": 31.0700,
            "lon": 121.7200,
            "alt": 0,
            "speed": 9,
            "heading": 288,
            "confidence": 0.87,
            "source_agent": "coastal-surface-radar",
            "metadata": {
                "scenario_role": "surface_group_lead",
                "ais_status": "intermittent",
                "sea_state": 3,
                "operation_phase": phase,
            },
        },
        {
            "id": "auto-surface-2",
            "object_type": "ship",
            "lat": 31.0810,
            "lon": 121.7370,
            "alt": 0,
            "speed": 8,
            "heading": 286,
            "confidence": 0.85,
            "source_agent": "coastal-surface-radar",
            "metadata": {
                "scenario_role": "surface_group_trail",
                "ais_status": "intermittent",
                "sea_state": 3,
                "operation_phase": phase,
            },
        },
        {
            "id": "auto-low-uav-1",
            "object_type": "uav",
            "lat": 31.2550,
            "lon": 121.5450,
            "alt": 950,
            "speed": 42,
            "heading": 260,
            "confidence": 0.82,
            "source_agent": "passive-rf-fusion",
            "metadata": {
                "scenario_role": "low_altitude_uav_track",
                "sensor_note": "passive RF bearing plus short radar hits",
                "track_stability": "medium",
                "operation_phase": phase,
            },
        },
        {
            "id": "auto-unknown-intermittent-1",
            "object_type": "unknown",
            "lat": 31.1850,
            "lon": 121.4050,
            "alt": 2700,
            "speed": 82,
            "heading": 35,
            "confidence": 0.56,
            "source_agent": "multi-sensor-fusion",
            "metadata": {
                "scenario_role": "unknown_intermittent_track",
                "identification": "unresolved",
                "sensor_note": "short radar dwell, no cooperative ID",
                "operation_phase": phase,
            },
        },
    ]

    detections = []
    for spec in specs:
        heading = float(spec["heading"])
        speed = float(spec["speed"])
        confidence = float(spec["confidence"])
        metadata = {
            "simulated": True,
            "auto_frame": frame_index,
            "exercise_name": "fictional_littoral_joint_awareness_demo",
            "safety_note": "simulation-only situation awareness; no engagement advice",
            **dict(spec.get("metadata", {})),
        }
        if spec["id"] == "auto-low-uav-1" and frame_index >= 35:
            heading = 235.0
            speed = 54.0
            metadata["simulated_behavior_change"] = "low-altitude UAV turns toward logistics pier during monitoring phase"
        if spec["id"] == "auto-unknown-intermittent-1" and frame_index >= 45:
            heading = 122.0
            speed = 128.0
            confidence = 0.39
            metadata["simulated_anomaly"] = "unknown track changed heading and speed during anomaly escalation phase"
        lat, lon = _project_demo_position(spec, frame_index, speed, heading)
        vx, vy = speed_heading_to_velocity(speed, heading)
        detections.append(
            {
                "detection_id": f"{spec['id']}-f{frame_index:02d}",
                "object_type": spec["object_type"],
                "timestamp": timestamp,
                "lat": lat,
                "lon": lon,
                "alt": float(spec["alt"]) + math.sin(frame_index / 4.0) * 15.0,
                "speed": speed,
                "heading": heading,
                "confidence": confidence,
                "source_agent": spec["source_agent"],
                "metadata": metadata,
            }
        )

    payload = wrap_payload(f"auto-demo-frame-{frame_index:02d}", detections)
    payload["scene"]["operation_phase"] = phase
    payload["scene"]["frame_index"] = frame_index
    return payload


def generate_long_operation_sequence(frame_count: int = 90) -> Dict[str, object]:
    """Generate the complete deterministic landing-demo sequence.

    The sequence is intentionally returned as data rather than written to disk so
    tests, evaluation scripts, and A2A smoke scripts can all use the exact same
    scenario source.
    """

    frames = [generate_auto_demo_frame(index) for index in range(frame_count)]
    return {
        "scenario_id": "coastal_joint_operation_90_frames",
        "scenario_name": "Fictional Coastal Joint Operation Situation-Awareness Sequence",
        "frame_count": frame_count,
        "frame_interval_s": 1,
        "description": (
            "Simulation-only long sequence for validating track maintenance, "
            "trajectory prediction, group detection, protected-asset impact, "
            "and unified attention-priority ranking."
        ),
        "phases": [
            {"name": "phase_1_initial_detection", "frame_start": 0, "frame_end": 14},
            {"name": "phase_2_track_stabilization", "frame_start": 15, "frame_end": 34},
            {"name": "phase_3_protected_asset_monitoring", "frame_start": 35, "frame_end": 44},
            {"name": "phase_4_anomaly_escalation", "frame_start": 45, "frame_end": 74},
            {"name": "phase_5_sustained_presence", "frame_start": 75, "frame_end": frame_count - 1},
        ],
        "protected_assets": default_protected_assets(),
        "frames": frames,
        "safety_boundary": "Simulation-only situation awareness; no weapon control, no engagement advice.",
    }


def _project_demo_position(
    spec: Dict[str, object],
    frame_index: int,
    current_speed: float,
    current_heading: float,
) -> tuple[float, float]:
    base_lat = float(spec["lat"])
    base_lon = float(spec["lon"])
    original_speed = float(spec["speed"])
    original_heading = float(spec["heading"])
    switch_frame = None
    if spec["id"] == "auto-low-uav-1":
        switch_frame = 35
    elif spec["id"] == "auto-unknown-intermittent-1":
        switch_frame = 45

    if switch_frame is None or frame_index < switch_frame:
        vx, vy = speed_heading_to_velocity(current_speed, current_heading)
        return project_position(base_lat, base_lon, vx, vy, frame_index)

    old_vx, old_vy = speed_heading_to_velocity(original_speed, original_heading)
    switch_lat, switch_lon = project_position(base_lat, base_lon, old_vx, old_vy, switch_frame)
    new_vx, new_vy = speed_heading_to_velocity(current_speed, current_heading)
    return project_position(switch_lat, switch_lon, new_vx, new_vy, frame_index - switch_frame)


def _operation_phase(frame_index: int) -> str:
    if frame_index < 15:
        return "phase_1_initial_detection"
    if frame_index < 35:
        return "phase_2_track_stabilization"
    if frame_index < 45:
        return "phase_3_protected_asset_monitoring"
    if frame_index < 75:
        return "phase_4_anomaly_escalation"
    return "phase_5_sustained_presence"

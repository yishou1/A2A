from __future__ import annotations

from copy import deepcopy
from math import cos, radians, sin
from typing import Any, Dict, List


DEFAULT_TEMPLATE_ID = "landing_corridor_multiframe"
_FRAME_PHASES = [
    "initial_detection",
    "track_stabilization",
    "threat_refinement",
    "plan_handoff",
    "plan_review",
    "closure_assessment",
]


def _threat_label(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"


def _kind_from_object_type(object_type: str, count_hint: int = 1) -> str:
    if object_type == "ship":
        return "surface_group" if count_hint > 1 else "surface_contact"
    if object_type == "aircraft":
        return "aircraft_formation" if count_hint > 1 else "aircraft_contact"
    if object_type == "uav":
        return "uav_swarm" if count_hint > 1 else "uav_contact"
    return "unknown_contact"


def _project_position(lat: float, lon: float, speed_mps: float, heading_deg: float, dt_s: float) -> tuple[float, float]:
    heading_rad = radians(heading_deg)
    north_m = cos(heading_rad) * speed_mps * dt_s
    east_m = sin(heading_rad) * speed_mps * dt_s
    next_lat = lat + north_m / 111_320.0
    lon_scale = max(0.01, cos(radians(lat)))
    next_lon = lon + east_m / (111_320.0 * lon_scale)
    return next_lat, next_lon


def _protected_asset(
    asset_id: str,
    asset_name: str,
    asset_type: str,
    lat: float,
    lon: float,
    *,
    radius_m: float,
    criticality: float,
    role: str,
) -> Dict[str, Any]:
    return {
        "asset_id": asset_id,
        "asset_name": asset_name,
        "asset_type": asset_type,
        "lat": lat,
        "lon": lon,
        "alt": 0.0,
        "protection_radius_m": radius_m,
        "criticality": criticality,
        "status": "protected",
        "metadata": {
            "role": role,
            "side": "blue",
        },
    }


def _build_frames(
    *,
    scenario_name: str,
    mission_id: str,
    contacts: List[Dict[str, Any]],
    protected_assets: List[Dict[str, Any]],
    zone_lat: float,
    zone_lon: float,
    radius_m: float,
    frame_count: int = 5,
    dt_s: float = 12.0,
) -> List[Dict[str, Any]]:
    states: Dict[str, Dict[str, float]] = {}
    for contact in contacts:
        states[contact["contact_id"]] = {
            "lat": float(contact["track_seed"]["lat"]),
            "lon": float(contact["track_seed"]["lon"]),
            "alt": float(contact["track_seed"].get("alt", 0.0)),
            "speed": float(contact["track_seed"].get("speed", 0.0)),
            "heading": float(contact["track_seed"].get("heading", 0.0)),
        }

    frames: List[Dict[str, Any]] = []
    base_timestamp = 4000.0
    for frame_index in range(frame_count):
        detections: List[Dict[str, Any]] = []
        phase = _FRAME_PHASES[min(frame_index, len(_FRAME_PHASES) - 1)]
        for contact in contacts:
            contact_id = contact["contact_id"]
            state = states[contact_id]
            metadata = {
                "source_contact_id": contact_id,
                "source_location": contact["location"],
                "intent": contact.get("intent"),
                "label": "hostile" if float(contact["threat_level"]) >= 0.6 else "unknown",
                "affiliation": "red" if float(contact["threat_level"]) >= 0.6 else "unknown",
                "threat_level": _threat_label(float(contact["threat_level"])),
                "knowledge_ref": f"ENT-{contact_id}",
                "knowledge_relations": [
                    {"predicate": "threatens", "object": "mission_area"},
                    {"predicate": "observed_in", "object": scenario_name},
                ],
                "scenario_role": contact["metadata"].get("scenario_role", contact_id),
                "operation_phase": phase,
                "simulation_only": True,
            }
            extra_metadata = dict(contact.get("metadata", {}))
            extra_metadata.pop("scenario_role", None)
            metadata.update(extra_metadata)
            detections.append(
                {
                    "detection_id": f"{contact_id}-f{frame_index:02d}",
                    "object_type": contact["object_type"],
                    "timestamp": base_timestamp + frame_index * dt_s,
                    "lat": round(state["lat"], 6),
                    "lon": round(state["lon"], 6),
                    "alt": round(state["alt"], 2),
                    "speed": round(state["speed"], 2),
                    "heading": round(state["heading"], 2),
                    "confidence": contact["confidence"],
                    "source_agent": contact["sensor_source"],
                    "metadata": metadata,
                }
            )

            next_speed = max(0.0, state["speed"] + float(contact["track_seed"].get("speed_delta_per_frame", 0.0)))
            next_heading = (state["heading"] + float(contact["track_seed"].get("turn_rate_deg_per_frame", 0.0))) % 360.0
            next_lat, next_lon = _project_position(state["lat"], state["lon"], next_speed, next_heading, dt_s)
            states[contact_id] = {
                "lat": next_lat,
                "lon": next_lon,
                "alt": state["alt"] + float(contact["track_seed"].get("climb_rate_m_per_frame", 0.0)),
                "speed": next_speed,
                "heading": next_heading,
            }

        frames.append(
            {
                "task_id": f"{mission_id}-frame-{frame_index:02d}",
                "message_type": "perception_result",
                "algorithm_level": "medium",
                "scene": {
                    "operation_name": scenario_name,
                    "operation_phase": phase,
                    "frame_index": frame_index,
                    "protected_zone_lat": zone_lat,
                    "protected_zone_lon": zone_lon,
                    "protected_radius_m": radius_m,
                    "protected_assets": deepcopy(protected_assets),
                },
                "detections": detections,
            }
        )
    return frames


def _contact(
    contact_id: str,
    *,
    display_name: str,
    object_type: str,
    location: str,
    threat_level: float,
    intent: str,
    sensor_source: str,
    confidence: float,
    lat: float,
    lon: float,
    alt: float,
    speed: float,
    heading: float,
    metadata: Dict[str, Any] | None = None,
    speed_delta_per_frame: float = 0.0,
    turn_rate_deg_per_frame: float = 0.0,
    climb_rate_m_per_frame: float = 0.0,
) -> Dict[str, Any]:
    meta = dict(metadata or {})
    count_hint = int(meta.pop("count_hint", 1))
    return {
        "contact_id": contact_id,
        "display_name": display_name,
        "kind": _kind_from_object_type(object_type, count_hint=count_hint),
        "object_type": object_type,
        "location": location,
        "threat_level": threat_level,
        "velocity": speed,
        "intent": intent,
        "confidence": confidence,
        "sensor_source": sensor_source,
        "metadata": meta,
        "track_seed": {
            "lat": lat,
            "lon": lon,
            "alt": alt,
            "speed": speed,
            "heading": heading,
            "speed_delta_per_frame": speed_delta_per_frame,
            "turn_rate_deg_per_frame": turn_rate_deg_per_frame,
            "climb_rate_m_per_frame": climb_rate_m_per_frame,
        },
    }


def _platform(
    platform_id: str,
    platform_type: str,
    readiness: float,
    munitions: int,
    location: str,
) -> Dict[str, Any]:
    return {
        "platform_id": platform_id,
        "platform_type": platform_type,
        "readiness": readiness,
        "munitions": munitions,
        "location": location,
    }


def _mission_payload(
    *,
    template_id: str,
    display_name: str,
    display_summary: str,
    scenario_name: str,
    objective: str,
    contacts: List[Dict[str, Any]],
    friendly_platforms: List[Dict[str, Any]],
    constraints: Dict[str, Any],
    environment: Dict[str, Any],
    intelligence_text: str,
    protected_assets: List[Dict[str, Any]],
    zone_lat: float,
    zone_lon: float,
    radius_m: float,
    success_threshold: float,
    max_replans: int,
    planning_focus: str,
    tags: List[str],
) -> Dict[str, Any]:
    mission_contacts = []
    for item in contacts:
        mission_contacts.append(
            {
                "contact_id": item["contact_id"],
                "kind": item["kind"],
                "location": item["location"],
                "threat_level": item["threat_level"],
                "velocity": item["velocity"],
                "intent": item["intent"],
                "metadata": {
                    "display_name": item["display_name"],
                    "object_type": item["object_type"],
                    "sensor_source": item["sensor_source"],
                    **item["metadata"],
                },
            }
        )

    frames = _build_frames(
        scenario_name=scenario_name,
        mission_id=template_id,
        contacts=contacts,
        protected_assets=protected_assets,
        zone_lat=zone_lat,
        zone_lon=zone_lon,
        radius_m=radius_m,
    )

    return {
        "objective": objective,
        "scenario_name": scenario_name,
        "mission_type": "integrated_demo",
        "contacts": mission_contacts,
        "friendly_platforms": friendly_platforms,
        "constraints": constraints,
        "environment": environment,
        "intelligence_text": intelligence_text,
        "success_threshold": success_threshold,
        "max_replans": max_replans,
        "demo_delay_ms": 180,
        "require_operator_approval": False,
        "simulation_mode": "safe",
        "scene": {
            "protected_zone_lat": zone_lat,
            "protected_zone_lon": zone_lon,
            "protected_radius_m": radius_m,
        },
        "protected_assets": protected_assets,
        "perception_frames": frames,
        "metadata": {
            "template_id": template_id,
            "display_name": display_name,
            "display_summary": display_summary,
            "planning_focus": planning_focus,
            "frame_count": len(frames),
            "tags": tags,
        },
    }


def _build_library() -> Dict[str, Dict[str, Any]]:
    landing_assets = [
        _protected_asset("landing-c2", "登陆通道指挥节点", "command_post", 31.2304, 121.4737, radius_m=9000, criticality=0.96, role="通道指挥与协同"),
        _protected_asset("landing-radar", "海岸警戒雷达站", "radar_site", 31.2620, 121.4380, radius_m=7600, criticality=0.89, role="前沿预警与跟踪"),
        _protected_asset("landing-pier", "后装保障码头", "logistics_node", 31.2180, 121.5290, radius_m=6800, criticality=0.84, role="补给与装卸保障"),
    ]
    landing_contacts = [
        _contact(
            "surface-lead-1",
            display_name="突防舰艇 1",
            object_type="ship",
            location="登陆通道南口",
            threat_level=0.84,
            intent="attack",
            sensor_source="coastal-surface-radar",
            confidence=0.9,
            lat=31.094,
            lon=121.676,
            alt=0.0,
            speed=10.5,
            heading=300.0,
            metadata={"scenario_role": "surface_group_lead", "count_hint": 2, "sea_state": 3},
        ),
        _contact(
            "surface-wing-2",
            display_name="突防舰艇 2",
            object_type="ship",
            location="登陆通道南口",
            threat_level=0.79,
            intent="attack",
            sensor_source="coastal-surface-radar",
            confidence=0.88,
            lat=31.101,
            lon=121.688,
            alt=0.0,
            speed=9.8,
            heading=298.0,
            metadata={"scenario_role": "surface_group_wing", "count_hint": 2, "sea_state": 3},
        ),
        _contact(
            "corridor-uav-3",
            display_name="低空侦察无人机",
            object_type="uav",
            location="登陆通道东侧",
            threat_level=0.67,
            intent="probe",
            sensor_source="passive-rf-fusion",
            confidence=0.81,
            lat=31.248,
            lon=121.557,
            alt=980.0,
            speed=22.0,
            heading=256.0,
            metadata={"scenario_role": "low_altitude_probe", "track_stability": "medium"},
            turn_rate_deg_per_frame=-2.0,
        ),
    ]

    air_raid_assets = [
        _protected_asset("radar-main", "远程预警雷达站", "radar_site", 30.852, 121.056, radius_m=8200, criticality=0.95, role="防空探测骨干"),
        _protected_asset("relay-c2", "海岸接力指挥所", "command_post", 30.826, 121.098, radius_m=7600, criticality=0.88, role="空情接力与指挥"),
    ]
    air_raid_contacts = [
        _contact(
            "air-lead-1",
            display_name="空中编队长机",
            object_type="aircraft",
            location="雷达站东北外侧",
            threat_level=0.8,
            intent="attack",
            sensor_source="coastal-air-radar",
            confidence=0.93,
            lat=30.968,
            lon=121.175,
            alt=7200.0,
            speed=210.0,
            heading=228.0,
            metadata={"scenario_role": "air_formation_lead", "count_hint": 3, "sensor_mode": "track-while-scan"},
        ),
        _contact(
            "air-wing-2",
            display_name="空中编队僚机 1",
            object_type="aircraft",
            location="雷达站东北外侧",
            threat_level=0.76,
            intent="attack",
            sensor_source="coastal-air-radar",
            confidence=0.91,
            lat=30.975,
            lon=121.187,
            alt=7150.0,
            speed=208.0,
            heading=230.0,
            metadata={"scenario_role": "air_formation_wing", "count_hint": 3, "formation_hint": "wedge"},
        ),
        _contact(
            "air-wing-3",
            display_name="空中编队僚机 2",
            object_type="aircraft",
            location="雷达站东北外侧",
            threat_level=0.74,
            intent="probe",
            sensor_source="coastal-air-radar",
            confidence=0.9,
            lat=30.961,
            lon=121.163,
            alt=7280.0,
            speed=212.0,
            heading=226.0,
            metadata={"scenario_role": "air_formation_wing", "count_hint": 3, "formation_hint": "wedge"},
        ),
        _contact(
            "escort-uav-4",
            display_name="伴随中继无人机",
            object_type="uav",
            location="雷达站东北外侧",
            threat_level=0.58,
            intent="relay",
            sensor_source="passive-rf-fusion",
            confidence=0.79,
            lat=30.914,
            lon=121.131,
            alt=1500.0,
            speed=32.0,
            heading=235.0,
            metadata={"scenario_role": "relay_uav"},
        ),
    ]

    swarm_assets = [
        _protected_asset("pier-main", "后勤补给码头", "logistics_node", 31.182, 121.618, radius_m=7200, criticality=0.91, role="后勤保障核心"),
        _protected_asset("fuel-jetty", "油料栈桥", "harbor_facility", 31.166, 121.596, radius_m=6400, criticality=0.85, role="油料转运"),
        _protected_asset("medical-point", "医疗集结点", "medical_node", 31.204, 121.602, radius_m=5600, criticality=0.76, role="伤员转运"),
    ]
    swarm_contacts = [
        _contact(
            "swarm-uav-1",
            display_name="低空无人机 1",
            object_type="uav",
            location="补给码头东南低空",
            threat_level=0.72,
            intent="probe",
            sensor_source="multi-sensor-fusion",
            confidence=0.82,
            lat=31.246,
            lon=121.692,
            alt=680.0,
            speed=28.0,
            heading=236.0,
            metadata={"scenario_role": "swarm_lead", "count_hint": 3, "sensor_note": "short radar bursts"},
        ),
        _contact(
            "swarm-uav-2",
            display_name="低空无人机 2",
            object_type="uav",
            location="补给码头东南低空",
            threat_level=0.69,
            intent="probe",
            sensor_source="multi-sensor-fusion",
            confidence=0.79,
            lat=31.251,
            lon=121.701,
            alt=700.0,
            speed=27.0,
            heading=238.0,
            metadata={"scenario_role": "swarm_wing", "count_hint": 3, "sensor_note": "short radar bursts"},
        ),
        _contact(
            "swarm-uav-3",
            display_name="低空无人机 3",
            object_type="uav",
            location="补给码头东南低空",
            threat_level=0.66,
            intent="relay",
            sensor_source="multi-sensor-fusion",
            confidence=0.78,
            lat=31.238,
            lon=121.687,
            alt=720.0,
            speed=26.0,
            heading=234.0,
            metadata={"scenario_role": "swarm_support", "count_hint": 3, "sensor_note": "short radar bursts"},
        ),
        _contact(
            "relay-unknown-4",
            display_name="间歇不明目标",
            object_type="unknown",
            location="补给码头南侧外缘",
            threat_level=0.51,
            intent="unknown",
            sensor_source="passive-rf-fusion",
            confidence=0.61,
            lat=31.214,
            lon=121.641,
            alt=2400.0,
            speed=68.0,
            heading=212.0,
            metadata={"scenario_role": "intermittent_unknown", "track_quality_hint": "low"},
            turn_rate_deg_per_frame=4.0,
        ),
    ]

    mixed_assets = [
        _protected_asset("harbor-gate", "港口入口航道", "civil_infrastructure", 31.022, 121.583, radius_m=7000, criticality=0.9, role="港口主航道"),
        _protected_asset("oil-storage", "港内油料储区", "logistics_node", 31.044, 121.612, radius_m=6200, criticality=0.87, role="油料保障"),
    ]
    mixed_contacts = [
        _contact(
            "mixed-boat-1",
            display_name="近岸快艇",
            object_type="ship",
            location="港口南向入口",
            threat_level=0.71,
            intent="probe",
            sensor_source="coastal-surface-radar",
            confidence=0.86,
            lat=30.976,
            lon=121.641,
            alt=0.0,
            speed=14.0,
            heading=321.0,
            metadata={"scenario_role": "mixed_group_surface", "count_hint": 2},
        ),
        _contact(
            "mixed-uav-2",
            display_name="伴随低空无人机",
            object_type="uav",
            location="港口南向入口",
            threat_level=0.64,
            intent="relay",
            sensor_source="passive-rf-fusion",
            confidence=0.8,
            lat=30.992,
            lon=121.629,
            alt=520.0,
            speed=18.0,
            heading=316.0,
            metadata={"scenario_role": "mixed_group_uav", "count_hint": 2},
        ),
        _contact(
            "mixed-boat-3",
            display_name="第二艘近岸快艇",
            object_type="ship",
            location="港口南向入口",
            threat_level=0.68,
            intent="probe",
            sensor_source="coastal-surface-radar",
            confidence=0.84,
            lat=30.968,
            lon=121.653,
            alt=0.0,
            speed=13.5,
            heading=319.0,
            metadata={"scenario_role": "surface_support"},
        ),
    ]

    command_assets = [
        _protected_asset("command-core", "联合作战指挥节点", "command_post", 31.292, 121.412, radius_m=7800, criticality=0.95, role="联合作战指挥"),
        _protected_asset("comm-hub", "通信中继枢纽", "civil_infrastructure", 31.314, 121.436, radius_m=6500, criticality=0.83, role="通信与数据中继"),
        _protected_asset("ad-battery", "近程防空阵地", "radar_site", 31.271, 121.385, radius_m=6000, criticality=0.8, role="近程警戒"),
    ]
    command_contacts = [
        _contact(
            "command-unknown-1",
            display_name="间歇空中目标",
            object_type="unknown",
            location="指挥节点北侧外缘",
            threat_level=0.63,
            intent="unknown",
            sensor_source="multi-sensor-fusion",
            confidence=0.58,
            lat=31.368,
            lon=121.466,
            alt=2400.0,
            speed=74.0,
            heading=218.0,
            metadata={"scenario_role": "intermittent_air_track"},
            turn_rate_deg_per_frame=-6.0,
        ),
        _contact(
            "command-uav-2",
            display_name="边界试探无人机",
            object_type="uav",
            location="指挥节点东北外侧",
            threat_level=0.59,
            intent="probe",
            sensor_source="passive-rf-fusion",
            confidence=0.77,
            lat=31.341,
            lon=121.487,
            alt=860.0,
            speed=24.0,
            heading=232.0,
            metadata={"scenario_role": "boundary_probe"},
        ),
        _contact(
            "command-aircraft-3",
            display_name="远距高空平台",
            object_type="aircraft",
            location="指挥节点东侧远距",
            threat_level=0.56,
            intent="relay",
            sensor_source="coastal-air-radar",
            confidence=0.83,
            lat=31.402,
            lon=121.534,
            alt=6800.0,
            speed=178.0,
            heading=246.0,
            metadata={"scenario_role": "high_altitude_support"},
        ),
    ]

    return {
        "landing_corridor_multiframe": _mission_payload(
            template_id="landing_corridor_multiframe",
            display_name="登陆通道防护与突防编组压制",
            display_summary="5 帧连续点迹，含 2 艘突防舰艇与 1 架低空无人机，重点演示连续航迹、保护目标影响和表面编组识别。",
            scenario_name="landing-corridor-multiframe-demo",
            objective="Protect the landing corridor and keep the command post and logistics pier inside the safe envelope.",
            contacts=landing_contacts,
            friendly_platforms=[
                _platform("uav-alpha", "uav", 0.94, 2, "Blue-1"),
                _platform("battery-bravo", "artillery", 0.88, 3, "Blue-2"),
                _platform("ew-charlie", "ew", 0.86, 1, "Blue-3"),
            ],
            constraints={"no_real_execution": True, "keep_collateral_risk_low": True},
            environment={"weather": "clear", "sea_state": "moderate", "jamming_level": 0.18},
            intelligence_text="Recon indicates a two-ship hostile surface element approaching the landing corridor while one low-altitude UAV probes the protected zone.",
            protected_assets=landing_assets,
            zone_lat=31.2304,
            zone_lon=121.4737,
            radius_m=30000.0,
            success_threshold=0.6,
            max_replans=1,
            planning_focus="containment",
            tags=["登陆通道", "表面编组", "低空无人机"],
        ),
        "radar_station_air_raid": _mission_payload(
            template_id="radar_station_air_raid",
            display_name="雷达站方向空中编队靠近",
            display_summary="5 帧连续点迹，含 3 机空中编队与 1 架伴随无人机，重点演示空中编组和高空平台趋势预测。",
            scenario_name="radar-station-air-raid-demo",
            objective="Protect the long-range radar site and maintain early-warning continuity during an approaching air formation.",
            contacts=air_raid_contacts,
            friendly_platforms=[
                _platform("fighter-alpha", "fighter", 0.91, 4, "Blue-Air-1"),
                _platform("sam-bravo", "air_defense", 0.87, 6, "Blue-Air-2"),
                _platform("uav-charlie", "uav", 0.83, 2, "Blue-Air-3"),
            ],
            constraints={"no_real_execution": True, "preserve_sensor_coverage": True},
            environment={"weather": "high_cloud", "jamming_level": 0.22},
            intelligence_text="Air surveillance shows a three-aircraft hostile formation tightening toward the radar site with one relay UAV trailing the formation.",
            protected_assets=air_raid_assets,
            zone_lat=30.852,
            zone_lon=121.056,
            radius_m=26000.0,
            success_threshold=0.62,
            max_replans=1,
            planning_focus="air_denial",
            tags=["空中编队", "雷达站", "趋势预测"],
        ),
        "logistics_pier_uav_swarm": _mission_payload(
            template_id="logistics_pier_uav_swarm",
            display_name="补给码头低空无人机群试探",
            display_summary="5 帧连续点迹，含 3 架低空无人机与 1 个间歇不明目标，重点演示低空群目标跟踪与补给区影响排序。",
            scenario_name="logistics-pier-uav-swarm-demo",
            objective="Protect the logistics pier and keep sustainment throughput stable while monitoring the incoming low-altitude swarm.",
            contacts=swarm_contacts,
            friendly_platforms=[
                _platform("jammer-alpha", "ew", 0.9, 2, "Blue-Log-1"),
                _platform("uav-bravo", "uav", 0.89, 2, "Blue-Log-2"),
                _platform("battery-charlie", "air_defense", 0.84, 4, "Blue-Log-3"),
            ],
            constraints={"no_real_execution": True, "preserve_logistics_flow": True, "low_collateral_risk": True},
            environment={"weather": "hazy", "wind": "east_light", "jamming_level": 0.27},
            intelligence_text="Several low-altitude UAV tracks are converging toward the logistics pier while one intermittent contact remains outside the main approach corridor.",
            protected_assets=swarm_assets,
            zone_lat=31.182,
            zone_lon=121.618,
            radius_m=23000.0,
            success_threshold=0.58,
            max_replans=1,
            planning_focus="resource_preservation",
            tags=["补给码头", "无人机群", "低空监视"],
        ),
        "harbor_mixed_intrusion": _mission_payload(
            template_id="harbor_mixed_intrusion",
            display_name="港口入口混合目标试探",
            display_summary="5 帧连续点迹，含 2 艘快艇与 1 架伴随无人机，重点演示混合编组识别和港口入口保护目标影响。",
            scenario_name="harbor-mixed-intrusion-demo",
            objective="Protect the harbor entrance and oil-storage area while tracking a mixed boat-and-UAV probing pattern.",
            contacts=mixed_contacts,
            friendly_platforms=[
                _platform("patrol-alpha", "uav", 0.86, 2, "Blue-Harbor-1"),
                _platform("coastal-bravo", "artillery", 0.82, 2, "Blue-Harbor-2"),
                _platform("sensor-charlie", "radar", 0.9, 0, "Blue-Harbor-3"),
            ],
            constraints={"no_real_execution": True, "harbor_safety_first": True},
            environment={"weather": "clear", "sea_state": "slight", "jamming_level": 0.12},
            intelligence_text="A mixed set of fast boats and a low-altitude UAV is probing the harbor entrance and staying close enough to be treated as a coordinated pattern.",
            protected_assets=mixed_assets,
            zone_lat=31.022,
            zone_lon=121.583,
            radius_m=21000.0,
            success_threshold=0.57,
            max_replans=1,
            planning_focus="harbor_protection",
            tags=["港口入口", "混合编组", "油料区"],
        ),
        "command_node_border_probe": _mission_payload(
            template_id="command_node_border_probe",
            display_name="指挥节点周边边界试探",
            display_summary="5 帧连续点迹，含 1 个间歇不明空中目标、1 架边界试探无人机和 1 个远距高空平台，重点演示不明目标持续跟踪。",
            scenario_name="command-node-border-probe-demo",
            objective="Maintain confidence around the joint command node while distinguishing a transient unknown contact from a boundary-probing UAV.",
            contacts=command_contacts,
            friendly_platforms=[
                _platform("uav-delta", "uav", 0.92, 2, "Blue-C2-1"),
                _platform("ew-echo", "ew", 0.85, 1, "Blue-C2-2"),
                _platform("sam-foxtrot", "air_defense", 0.83, 5, "Blue-C2-3"),
            ],
            constraints={"no_real_execution": True, "maintain_command_continuity": True},
            environment={"weather": "broken_cloud", "jamming_level": 0.31},
            intelligence_text="Command-node defense sensors are observing one intermittent unknown air track, one probing UAV and one distant high-altitude support platform.",
            protected_assets=command_assets,
            zone_lat=31.292,
            zone_lon=121.412,
            radius_m=24000.0,
            success_threshold=0.59,
            max_replans=1,
            planning_focus="command_protection",
            tags=["指挥节点", "不明目标", "持续监视"],
        ),
    }


_MISSION_LIBRARY = _build_library()


def list_demo_missions() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for template_id, payload in _MISSION_LIBRARY.items():
        metadata = payload.get("metadata", {})
        items.append(
            {
                "template_id": template_id,
                "display_name": metadata.get("display_name", template_id),
                "display_summary": metadata.get("display_summary", ""),
                "scenario_name": payload.get("scenario_name"),
                "objective": payload.get("objective"),
                "contact_count": len(payload.get("contacts", [])),
                "friendly_count": len(payload.get("friendly_platforms", [])),
                "frame_count": len(payload.get("perception_frames", [])),
                "planning_focus": metadata.get("planning_focus", "default"),
                "tags": metadata.get("tags", []),
            }
        )
    return items


def get_demo_mission(template_id: str) -> Dict[str, Any]:
    if template_id not in _MISSION_LIBRARY:
        raise KeyError(f"Unknown demo mission template: {template_id}")
    return deepcopy(_MISSION_LIBRARY[template_id])


def get_default_demo_mission() -> Dict[str, Any]:
    return get_demo_mission(DEFAULT_TEMPLATE_ID)

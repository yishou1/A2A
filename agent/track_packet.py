"""将感知 tracks 规范为下游可消费的航迹层（含 history_path）。"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

# 与 track_threat / trajectory_predictor 对齐的粗粒度 object_type
_CLASS_TO_OBJECT_TYPE: dict[str, str] = {
    "airplane": "aircraft",
    "aircraft": "aircraft",
    "helicopter": "aircraft",
    "drone": "aircraft",
    "uav": "aircraft",
    "jet": "aircraft",
    "fighter": "aircraft",
    "ship": "ship",
    "boat": "ship",
    "vessel": "ship",
    "frigate": "ship",
    "destroyer": "ship",
    "carrier": "ship",
    "warship": "ship",
    "tank": "ground",
    "armor": "ground",
    "truck": "ground",
    "car": "ground",
    "bus": "ground",
    "person": "person",
}

PACKET_SCHEMA_VERSION = "1.0"

CONSUMER_GUIDE: dict[str, Any] = {
    "schema_version": PACKET_SCHEMA_VERSION,
    "sections": {
        "tracks": {
            "consumers": [
                "trajectory_predictor",
                "track_state_updater",
                "graph_relation_reasoner",
                "track_threat",
            ],
            "description": (
                "机器可读航迹：含 lat/lon/alt/speed/heading 与 history_path，"
                "供航迹预测与图关系推理直接消费。"
            ),
            "required_fields": [
                "track_id",
                "object_type",
                "timestamp",
                "lat",
                "lon",
                "history_path",
            ],
        },
        "targets": {
            "consumers": ["decision_planning", "artillery", "evaluator", "commander"],
            "description": "语义目标层：威胁、敌我、类别与 geo，供决策/评估/火力使用。",
            "required_fields": ["track_id", "class", "threat_level", "geo"],
        },
        "task_schedule": {
            "consumers": ["sensor_scheduler", "commander", "decision_planning"],
            "description": (
                "传感器任务分配与再攻击规划；由独立 task_scheduling_agent（AMOS JSON）产出，"
                "TIA 管线不再内嵌 MARL-PPO 调度。"
            ),
        },
        "output_attachments": {
            "consumers": ["evaluator", "bda", "commander", "visualization"],
            "description": "标注图等产物的对象存储 URI，供可视化与 BDA。",
        },
        "summary": {
            "consumers": ["commander", "decision_planning"],
            "description": "自然语言态势摘要。",
        },
        "routing": {
            "consumers": ["commander", "communication"],
            "description": "下游订阅与抗干扰路由建议。",
        },
    },
}


def _utc_timestamp(value: Any | None = None) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return datetime.now(timezone.utc).timestamp()


def map_object_type(class_name: str | None) -> str:
    key = str(class_name or "unknown").strip().lower()
    if key in _CLASS_TO_OBJECT_TYPE:
        return _CLASS_TO_OBJECT_TYPE[key]
    for token, mapped in _CLASS_TO_OBJECT_TYPE.items():
        if token in key:
            return mapped
    return "unknown"


def _geo_dict(track: dict[str, Any]) -> dict[str, Any]:
    geo = track.get("geo")
    return geo if isinstance(geo, dict) else {}


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlmb = math.radians(lon2 - lon1)
    y = math.sin(dlmb) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlmb)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def history_point_from_track(
    track: dict[str, Any],
    *,
    timestamp: Any | None = None,
) -> dict[str, Any] | None:
    """从单帧 track 提取 history_path 点；缺 lat/lon 则返回 None。"""
    geo = _geo_dict(track)
    lat = geo.get("lat", track.get("lat"))
    lon = geo.get("lon", track.get("lon"))
    if lat is None or lon is None:
        return None
    ts = _utc_timestamp(timestamp or track.get("timestamp") or geo.get("timestamp"))
    alt = geo.get("alt_m", track.get("alt", track.get("alt_m", 0.0)))
    point: dict[str, Any] = {
        "timestamp": ts,
        "lat": float(lat),
        "lon": float(lon),
        "alt": float(alt) if alt is not None else 0.0,
        "confidence": float(track.get("confidence", geo.get("confidence", 0.0)) or 0.0),
    }
    if track.get("speed") is not None:
        point["speed"] = float(track["speed"])
    if track.get("heading") is not None:
        point["heading"] = float(track["heading"])
    return point


def _enrich_kinematics(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """由相邻 history 点补全 speed / heading（m/s, deg）。"""
    if len(points) < 2:
        return points
    out = [dict(p) for p in points]
    for i in range(1, len(out)):
        prev, cur = out[i - 1], out[i]
        dt = float(cur["timestamp"]) - float(prev["timestamp"])
        if dt <= 0:
            continue
        dist = _haversine_m(prev["lat"], prev["lon"], cur["lat"], cur["lon"])
        if "speed" not in cur:
            cur["speed"] = round(dist / dt, 3)
        if "heading" not in cur:
            cur["heading"] = round(
                _bearing_deg(prev["lat"], prev["lon"], cur["lat"], cur["lon"]), 2
            )
    # 首点继承次点运动学，便于下游至少有一组 speed/heading
    if "speed" not in out[0] and "speed" in out[1]:
        out[0]["speed"] = out[1]["speed"]
    if "heading" not in out[0] and "heading" in out[1]:
        out[0]["heading"] = out[1]["heading"]
    return out


def accumulate_track_history(
    current_tracks: list[dict[str, Any]],
    prior_tracks: list[dict[str, Any]] | None = None,
    *,
    timestamp: Any | None = None,
    max_history: int = 32,
) -> list[dict[str, Any]]:
    """
    跨帧累积 history_path，并写出下游标准航迹字段。

    返回的每条 track 同时保留感知层字段（bbox/geo 等）与下游字段。
    """
    prior_by_id = {
        str(t.get("track_id")): t
        for t in (prior_tracks or [])
        if t.get("track_id") is not None
    }
    ts = _utc_timestamp(timestamp)
    merged: list[dict[str, Any]] = []

    for track in current_tracks:
        tid = str(track.get("track_id") or "")
        if not tid:
            continue
        prior = prior_by_id.get(tid, {})
        history: list[dict[str, Any]] = []
        for item in prior.get("history_path") or []:
            if isinstance(item, dict) and "lat" in item and "lon" in item:
                history.append(dict(item))

        point = history_point_from_track(track, timestamp=ts)
        if point is not None:
            if history and abs(float(history[-1].get("timestamp", 0)) - point["timestamp"]) < 1e-6:
                history[-1] = point
            else:
                history.append(point)
        history = _enrich_kinematics(history)[-max_history:]

        geo = _geo_dict(track)
        lat = geo.get("lat", track.get("lat"))
        lon = geo.get("lon", track.get("lon"))
        alt = geo.get("alt_m", track.get("alt", 0.0))
        latest = history[-1] if history else {}

        record = dict(track)
        record.update(
            {
                "track_id": tid,
                "object_type": map_object_type(
                    track.get("class_name") or track.get("object_type") or prior.get("object_type")
                ),
                "class_name": track.get("class_name") or prior.get("class_name", "unknown"),
                "timestamp": float(latest.get("timestamp", ts)),
                "lat": float(lat) if lat is not None else latest.get("lat"),
                "lon": float(lon) if lon is not None else latest.get("lon"),
                "alt": float(alt) if alt is not None else float(latest.get("alt", 0.0) or 0.0),
                "speed": latest.get("speed", track.get("speed")),
                "heading": latest.get("heading", track.get("heading")),
                "confidence": float(track.get("confidence", latest.get("confidence", 0.0)) or 0.0),
                "history_path": history,
            }
        )
        merged.append(record)

    return merged


def packet_to_trajectory_request(packet: dict[str, Any]) -> dict[str, Any]:
    """intelligence_packet → trajectory_predictor 输入。"""
    tracks_out: list[dict[str, Any]] = []
    for track in packet.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        if track.get("lat") is None or track.get("lon") is None:
            continue
        tracks_out.append(
            {
                "track_id": track.get("track_id"),
                "object_type": track.get("object_type") or map_object_type(track.get("class_name")),
                "timestamp": track.get("timestamp"),
                "lat": track.get("lat"),
                "lon": track.get("lon"),
                "alt": track.get("alt", 0.0),
                "speed": track.get("speed"),
                "heading": track.get("heading"),
                "confidence": track.get("confidence", 0.0),
                "history_path": track.get("history_path") or [],
            }
        )
    return {"tracks": tracks_out}

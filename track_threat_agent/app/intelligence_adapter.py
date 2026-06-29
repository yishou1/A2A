"""情报数据适配器：将 TacticalIntelligenceAgent 输出格式转换为 track_threat_agent 的 Detection 格式。

同门（TacticalIntelligenceAgent）输出格式:
    {
      "targets": [
        {
          "track_id": "T-0001",
          "class": "bus",                              # COCO 风格类别名
          "label": "hostile",
          "affiliation": "red",
          "threat_level": "high",
          "geo": {"lat": 30.512, "lon": 114.381, "alt_m": 120.0},
          "damage_score": null,
          "confidence": 0.9882,
          "knowledge_ref": "ENT-T-0001"
        }
      ],
      "semantic_vector": [...],
      "knowledge_graph": {...},
      "routing": {...},
      "provenance": {...}
    }

track_threat_agent 需要的 Detection 格式:
    Detection(
        detection_id: str,          # 来自 track_id
        object_type: ObjectType,    # "aircraft"|"ship"|"uav"|"unknown"
        timestamp: float,           # 时间戳（从 payload 或当前时间推算）
        lat: float,                 # 来自 geo.lat
        lon: float,                 # 来自 geo.lon
        alt: float,                 # 来自 geo.alt_m
        speed: float,               # 从连续两帧位置差推算
        heading: float,             # 从连续两帧位置差推算
        confidence: float,          # 来自 confidence
        source_agent: str,          # 标记来源
        metadata: dict,             # 保留原始字段
    )

核心设计:
    - 单帧只有位置，没有速度/航向 → 首次出现的 track_id，speed/heading 默认为 0
    - 同一 track_id 连续两帧出现 → 用 haversine 距离 ÷ 时间差算 speed，
      用位置差的方向算 heading
    - 帧间缓存（_detection_history）记录每个 track_id 上次的位置和时间
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional

from .models import Detection
from .utils import haversine_m, velocity_to_speed_heading


# ---------------------------------------------------------------------------
# COCO 类别名 → 军用目标类型 映射表
# ---------------------------------------------------------------------------
CLASS_TO_OBJECT_TYPE: Dict[str, str] = {
    # 空中目标
    "airplane": "aircraft",
    "aeroplane": "aircraft",
    "aircraft": "aircraft",
    "helicopter": "aircraft",
    "jet": "aircraft",
    "drone": "uav",
    "uav": "uav",
    "quadcopter": "uav",
    # 水面目标
    "ship": "ship",
    "boat": "ship",
    "vessel": "ship",
    "warship": "ship",
    "submarine": "ship",
    "carrier": "ship",
    # 车辆 / 地面目标 → 不属于 air/ship/uav，归为 unknown
    # 但跟踪器仍可追踪其位置，预测模型用默认参数
    "bus": "unknown",
    "car": "unknown",
    "truck": "unknown",
    "motorcycle": "unknown",
    "bicycle": "unknown",
    "train": "unknown",
    "tank": "unknown",
    "armored": "unknown",
    "military_vehicle": "unknown",
    # 人员 → unknown
    "person": "unknown",
    "people": "unknown",
    "soldier": "unknown",
    # 其他
    "fire hydrant": "unknown",
    "handbag": "unknown",
    "backpack": "unknown",
    "suitcase": "unknown",
    "bird": "unknown",
    "cat": "unknown",
    "dog": "unknown",
}


def map_class_to_object_type(class_name: str) -> str:
    """将 COCO 风格类别名映射到军用目标类型。

    Args:
        class_name: 同门输出的 ``target.class`` 字段值。

    Returns:
        "aircraft" | "ship" | "uav" | "unknown"
    """
    if not class_name:
        return "unknown"
    return CLASS_TO_OBJECT_TYPE.get(class_name.strip().lower(), "unknown")


# ---------------------------------------------------------------------------
# 帧间缓存：用于从连续两帧推算 speed / heading
# ---------------------------------------------------------------------------
class DetectionFrameCache:
    """记录每个 track_id 上一次出现的位置和时间，用于推算运动状态。"""

    def __init__(self) -> None:
        self._cache: Dict[str, Dict[str, float]] = {}

    def record(self, track_id: str, lat: float, lon: float, alt: float, timestamp: float) -> None:
        """记录当前帧的检测位置和时间。"""
        self._cache[track_id] = {
            "lat": lat,
            "lon": lon,
            "alt": alt,
            "timestamp": timestamp,
        }

    def get_previous(self, track_id: str) -> Optional[Dict[str, float]]:
        """获取该 track_id 上一帧的位置和时间。"""
        return self._cache.get(track_id)

    def compute_speed_heading(
        self,
        track_id: str,
        lat: float,
        lon: float,
        timestamp: float,
    ) -> tuple[float, float]:
        """根据当前帧和上一帧的位置差，推算 speed (m/s) 和 heading (deg)。

        若该 track_id 首次出现（无历史记录），返回 (0.0, 0.0)。
        """
        prev = self.get_previous(track_id)
        if prev is None:
            return 0.0, 0.0

        dt = timestamp - prev["timestamp"]
        if dt <= 0.0:
            return 0.0, 0.0

        # 用 haversine 公式算两点间距离
        distance_m = haversine_m(prev["lat"], prev["lon"], lat, lon)

        # 用经纬度差折算东西/南北位移（米），再算 heading
        cos_lat = max(0.01, math.cos(math.radians(prev["lat"])))
        d_lat = lat - prev["lat"]
        d_lon = lon - prev["lon"]
        north_m = d_lat * 111_320.0
        east_m = d_lon * 111_320.0 * cos_lat

        speed, heading = velocity_to_speed_heading(east_m / dt, north_m / dt)

        # 速度下限校验：距离极小（静止/噪声）时 speed 趋近 0
        if distance_m < 0.5:
            speed = 0.0
            heading = 0.0

        return speed, heading

    def reset(self) -> None:
        """清空所有帧间缓存。"""
        self._cache.clear()


# ---------------------------------------------------------------------------
# 全局帧缓存实例（进程级，与 tracker 生命周期一致）
# ---------------------------------------------------------------------------
_frame_cache = DetectionFrameCache()


def get_frame_cache() -> DetectionFrameCache:
    return _frame_cache


# ---------------------------------------------------------------------------
# 核心转换函数
# ---------------------------------------------------------------------------
def convert_target_to_detection(
    target: Dict[str, Any],
    timestamp: float,
    track_id: Optional[str] = None,
    knowledge_relations: Optional[List[Dict[str, Any]]] = None,
) -> Detection:
    """将单个 target（同门格式）转换为 Detection。

    自动从帧间缓存推算 speed 和 heading：若该 track_id 已有历史记录，
    则用两帧位置差计算运动状态；否则 speed/heading 默认为 0。

    Args:
        target: 同门输出中的单个 target 字典。
        timestamp: 当前帧的 Unix 时间戳。
        track_id: 显式指定的 track_id；若为 None 则从 target["track_id"] 读取。

    Returns:
        填充好所有字段的 Detection 对象。
    """
    tid = track_id or target.get("track_id", "")
    geo = target.get("geo", {}) or {}
    lat = float(geo.get("lat", 0.0))
    lon = float(geo.get("lon", 0.0))
    alt = float(geo.get("alt_m", geo.get("alt", 0.0)))
    confidence = float(target.get("confidence", 0.5))
    class_name = str(target.get("class", "unknown"))
    object_type = map_class_to_object_type(class_name)

    # 从帧间缓存推算 speed / heading
    speed, heading = _frame_cache.compute_speed_heading(tid, lat, lon, timestamp)

    # 记录当前帧，供下一次推算使用
    _frame_cache.record(tid, lat, lon, alt, timestamp)

    # 把同门的其他字段打包进 metadata，不丢失信息
    metadata: Dict[str, Any] = {
        "source_class": class_name,
        "label": target.get("label", ""),
        "affiliation": target.get("affiliation", ""),
        "threat_level": target.get("threat_level", ""),
        "knowledge_ref": target.get("knowledge_ref", ""),
        "knowledge_relations": list(knowledge_relations or []),
        "damage_score": target.get("damage_score"),
        "adapted_by": "intelligence_adapter",
    }

    return Detection(
        detection_id=tid,
        object_type=object_type,
        timestamp=timestamp,
        lat=lat,
        lon=lon,
        alt=alt,
        speed=speed,
        heading=heading,
        confidence=confidence,
        source_agent="TacticalIntelligenceAgent",
        metadata=metadata,
    )


def convert_intelligence_to_detections(
    intelligence_payload: Dict[str, Any],
    timestamp: Optional[float] = None,
) -> List[Detection]:
    """将整份情报 payload（同门 TacticalIntelligenceAgent 格式）转换为 Detection 列表。

    自动处理 ``targets`` 字段，并为每个 target 调用 :func:`convert_target_to_detection`。

    Args:
        intelligence_payload: 同门输出的完整 JSON，必须包含 ``targets`` 字段。
        timestamp: 帧时间戳；若为 None 则用 ``created_at`` 或 ``time.time()``。

    Returns:
        Detection 对象列表。
    """
    targets = intelligence_payload.get("targets", [])
    if not targets:
        return []

    # 时间戳优先级: 显式传入 > payload.created_at > 当前时间
    if timestamp is None:
        raw_ts = intelligence_payload.get("created_at")
        if isinstance(raw_ts, str):
            # ISO 8601 → Unix timestamp（简化处理）
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                timestamp = dt.timestamp()
            except (ValueError, AttributeError):
                timestamp = time.time()
        elif isinstance(raw_ts, (int, float)):
            timestamp = float(raw_ts)
        else:
            timestamp = time.time()

    relations_by_entity = _relations_by_entity(intelligence_payload.get("knowledge_graph", {}) or {})
    detections: List[Detection] = []
    for target in targets:
        try:
            knowledge_ref = str(target.get("knowledge_ref", ""))
            detection = convert_target_to_detection(
                target,
                timestamp,
                knowledge_relations=relations_by_entity.get(knowledge_ref, []),
            )
            detections.append(detection)
        except Exception:
            # 单个 target 转换失败不影响整体
            continue

    return detections


def _relations_by_entity(knowledge_graph: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    relations: Dict[str, List[Dict[str, Any]]] = {}
    for node in knowledge_graph.get("nodes", []) or []:
        entity_id = str(node.get("entity_id", ""))
        if not entity_id:
            continue
        node_relations = node.get("relations", []) or []
        if isinstance(node_relations, list):
            relations[entity_id] = [
                {"predicate": item.get("predicate", ""), "object": item.get("object", "")}
                for item in node_relations
                if isinstance(item, dict)
            ]
    return relations


def extract_scene_from_intelligence(
    intelligence_payload: Dict[str, Any],
    override_scene: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """从同门的情报 payload 中提取或构建 scene 信息。

    优先使用显式传入的 override_scene；其次从 payload 的 knowledge_graph、
    routing 和 mission_id 中拼合作战场景。

    Args:
        intelligence_payload: 同门输出的完整 JSON。
        override_scene: 外部覆盖的场景信息。

    Returns:
        scene 字典，至少包含 protected_zone 和 operation_name。
    """
    if override_scene:
        return dict(override_scene)

    scene: Dict[str, Any] = {
        "operation_name": intelligence_payload.get("mission_id", "unknown-mission"),
    }

    # 从 targets 的 geo 坐标推算作战区域中心
    targets = intelligence_payload.get("targets", [])
    if targets:
        lats = []
        lons = []
        for t in targets:
            geo = t.get("geo", {}) or {}
            if "lat" in geo and "lon" in geo:
                lats.append(float(geo["lat"]))
                lons.append(float(geo["lon"]))
        if lats and lons:
            scene["protected_zone_lat"] = sum(lats) / len(lats)
            scene["protected_zone_lon"] = sum(lons) / len(lons)
            # 用 targets 分布范围估算保护区半径
            from .utils import haversine_m as h
            center_lat = scene["protected_zone_lat"]
            center_lon = scene["protected_zone_lon"]
            max_dist = max(
                h(center_lat, center_lon, lat, lon)
                for lat, lon in zip(lats, lons)
            )
            scene["protected_radius_m"] = max(max_dist * 1.5, 5_000.0)
            scene["protected_assets"] = []

    # 从 knowledge_graph 和 routing 提取额外场景信息
    kg = intelligence_payload.get("knowledge_graph", {}) or {}
    routing = intelligence_payload.get("routing", {}) or {}
    provenance = intelligence_payload.get("provenance", {}) or {}

    scene["knowledge_graph_nodes"] = len(kg.get("nodes", []))
    scene["knowledge_graph_edges"] = len(kg.get("edges", []))
    scene["anti_jam_mode"] = routing.get("anti_jam_mode", False)
    scene["routing_destinations"] = [
        r.get("destination") for r in routing.get("routes", [])
    ]
    scene["provenance_summary"] = {
        "perception": list(provenance.get("perception", {}).keys()),
        "cognition": list(provenance.get("cognition", {}).keys()),
        "communication": list(provenance.get("communication", {}).keys()),
    }

    return scene


def is_intelligence_format(payload: Dict[str, Any]) -> bool:
    """判断输入是否同门 TacticalIntelligenceAgent 格式。

    检测规则：顶层包含 ``targets`` 字段即为情报格式。
    """
    return "targets" in payload and isinstance(payload["targets"], list)


def reset_adapter_cache() -> None:
    """重置帧间缓存（通常在 demo reset 时调用）。"""
    _frame_cache.reset()

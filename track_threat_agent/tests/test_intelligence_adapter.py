"""测试情报数据适配器：验证同门 TacticalIntelligenceAgent 格式 → Detection 格式转换。"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import pytest

# 将 track_threat_agent/app 加入 import path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.intelligence_adapter import (
    DetectionFrameCache,
    convert_intelligence_to_detections,
    convert_target_to_detection,
    extract_scene_from_intelligence,
    is_intelligence_format,
    map_class_to_object_type,
    reset_adapter_cache,
)

# ---------------------------------------------------------------------------
# 用同门真实数据 latest_for_agents.json（OP-IRON-VALLEY-2026 战场）
# ---------------------------------------------------------------------------
INTEL_SAMPLE = {
    "packet_id": "faef4e3c-test",
    "mission_id": "OP-IRON-VALLEY-2026",
    "created_at": "2026-06-03T01:32:42.390969Z",
    "summary": "summary tactical intelligence",
    "targets": [
        {
            "track_id": "T-0001",
            "class": "bus",
            "label": "hostile",
            "affiliation": "red",
            "threat_level": "high",
            "geo": {"lat": 30.512, "lon": 114.381, "alt_m": 120.0},
            "damage_score": None,
            "confidence": 0.9882,
            "knowledge_ref": "ENT-T-0001",
        },
        {
            "track_id": "T-0022",
            "class": "person",
            "label": "hostile",
            "affiliation": "unknown",
            "threat_level": "high",
            "geo": {"lat": 30.513, "lon": 114.382, "alt_m": 120.0},
            "damage_score": None,
            "confidence": 0.9881,
            "knowledge_ref": "ENT-T-0022",
        },
        {
            "track_id": "T-0003",
            "class": "person",
            "label": "hostile",
            "affiliation": "red",
            "threat_level": "high",
            "geo": {"lat": 30.514, "lon": 114.383, "alt_m": 120.0},
            "damage_score": None,
            "confidence": 0.988,
            "knowledge_ref": "ENT-T-0003",
        },
        {
            "track_id": "T-0027",
            "class": "handbag",
            "label": "hostile",
            "affiliation": "unknown",
            "threat_level": "high",
            "geo": {"lat": 30.518, "lon": 114.387, "alt_m": 120.0},
            "damage_score": None,
            "confidence": 0.9778,
            "knowledge_ref": "ENT-T-0027",
        },
    ],
    "semantic_vector": [0.01] * 512,
    "knowledge_graph": {
        "nodes": [
            {
                "entity_id": "ENT-T-0001",
                "type": "MilitaryUnit",
                "label": "hostile",
                "relations": [{"predicate": "threat_of", "object": "mission_area"}],
            }
        ],
        "edges": [{"predicate": "threat_of", "object": "mission_area"}],
    },
    "routing": {
        "routes": [
            {"destination": "command_agent", "channel": "fhss_backup", "reliability": 0.358},
        ],
        "anti_jam_mode": True,
    },
    "provenance": {
        "perception": {"RT-DETR+ODConv": "4 detections"},
        "cognition": {"SupCon+Meta-Learning": "4 classifications"},
        "communication": {"Knowledge-Semantic-Comm": "ratio=3.43"},
    },
}


# ==================== 类别映射测试 ====================

@pytest.mark.parametrize(
    "class_name, expected_type",
    [
        ("airplane", "aircraft"),
        ("helicopter", "aircraft"),
        ("drone", "uav"),
        ("uav", "uav"),
        ("ship", "ship"),
        ("boat", "ship"),
        ("bus", "unknown"),
        ("person", "unknown"),
        ("fire hydrant", "unknown"),
        ("handbag", "unknown"),
        ("", "unknown"),
        ("nonexistent_class", "unknown"),
    ],
)
def test_map_class_to_object_type(class_name, expected_type):
    assert map_class_to_object_type(class_name) == expected_type


# ==================== 格式检测测试 ====================

def test_is_intelligence_format_positive():
    assert is_intelligence_format({"targets": []}) is True
    assert is_intelligence_format({"targets": [{"track_id": "T-0001"}]}) is True


def test_is_intelligence_format_negative():
    assert is_intelligence_format({}) is False
    assert is_intelligence_format({"detections": []}) is False
    assert is_intelligence_format({"targets": "not_a_list"}) is False


# ==================== 帧缓存测试 ====================

def test_frame_cache_first_detection_returns_zero_speed():
    cache = DetectionFrameCache()
    speed, heading = cache.compute_speed_heading("T-0001", 30.512, 114.381, 1000.0)
    assert speed == 0.0
    assert heading == 0.0


def test_frame_cache_second_detection_computes_motion():
    cache = DetectionFrameCache()
    # 第一帧：在 (30.512, 114.381) at t=1000.0
    cache.record("T-0001", 30.512, 114.381, 120.0, 1000.0)
    # 第二帧：向北移动约 0.001° ≈ 111m，在 t=1010.0 (10s后)
    lat2 = 30.513  # +0.001° N ≈ 111m
    lon2 = 114.381  # same longitude → purely north
    speed, heading = cache.compute_speed_heading("T-0001", lat2, lon2, 1010.0)
    # 111m / 10s ≈ 11.1 m/s，航向应为 0°（正北）
    assert speed > 5.0, f"Expected substantial speed, got {speed}"
    assert speed < 20.0, f"Speed too high: {speed}"
    # 正北移动，heading 应接近 0°（±5°容忍度）
    heading_diff = min(abs(heading - 0), abs(heading - 360))
    assert heading_diff < 5.0 or abs(speed) < 0.1, f"Heading {heading} not near 0° for pure north motion"


def test_frame_cache_reset():
    cache = DetectionFrameCache()
    cache.record("T-0001", 30.512, 114.381, 120.0, 1000.0)
    speed, heading = cache.compute_speed_heading("T-0001", 30.513, 114.381, 1010.0)
    assert speed > 0.0
    cache.reset()
    speed2, heading2 = cache.compute_speed_heading("T-0001", 30.514, 114.381, 1020.0)
    assert speed2 == 0.0  # cache cleared, treated as first detection


def test_frame_cache_same_position_returns_zero():
    cache = DetectionFrameCache()
    cache.record("T-0001", 30.512, 114.381, 120.0, 1000.0)
    speed, heading = cache.compute_speed_heading("T-0001", 30.512, 114.381, 1010.0)
    assert speed == 0.0  # distance < 0.5m


# ==================== 核心转换测试 ====================

class TestConvertIntelligenceToDetections:
    def test_converts_all_targets(self):
        reset_adapter_cache()
        detections = convert_intelligence_to_detections(INTEL_SAMPLE)
        assert len(detections) == 4

    def test_detection_ids_from_track_ids(self):
        reset_adapter_cache()
        detections = convert_intelligence_to_detections(INTEL_SAMPLE)
        ids = {d.detection_id for d in detections}
        assert ids == {"T-0001", "T-0022", "T-0003", "T-0027"}

    def test_geo_flattened(self):
        reset_adapter_cache()
        detections = convert_intelligence_to_detections(INTEL_SAMPLE)
        t1 = next(d for d in detections if d.detection_id == "T-0001")
        assert t1.lat == 30.512
        assert t1.lon == 114.381
        assert t1.alt == 120.0

    def test_confidence_preserved(self):
        reset_adapter_cache()
        detections = convert_intelligence_to_detections(INTEL_SAMPLE)
        t1 = next(d for d in detections if d.detection_id == "T-0001")
        assert t1.confidence == 0.9882

    def test_source_agent_marked(self):
        reset_adapter_cache()
        detections = convert_intelligence_to_detections(INTEL_SAMPLE)
        for d in detections:
            assert d.source_agent == "TacticalIntelligenceAgent"

    def test_metadata_preserves_original_fields(self):
        reset_adapter_cache()
        detections = convert_intelligence_to_detections(INTEL_SAMPLE)
        t1 = next(d for d in detections if d.detection_id == "T-0001")
        assert t1.metadata["source_class"] == "bus"
        assert t1.metadata["label"] == "hostile"
        assert t1.metadata["affiliation"] == "red"
        assert t1.metadata["knowledge_ref"] == "ENT-T-0001"

    def test_object_type_mapping(self):
        reset_adapter_cache()
        detections = convert_intelligence_to_detections(INTEL_SAMPLE)
        t1 = next(d for d in detections if d.detection_id == "T-0001")
        t2 = next(d for d in detections if d.detection_id == "T-0027")
        assert t1.object_type == "unknown"  # bus → unknown
        assert t2.object_type == "unknown"  # handbag → unknown

    def test_empty_targets(self):
        payload = {**INTEL_SAMPLE, "targets": []}
        detections = convert_intelligence_to_detections(payload)
        assert detections == []

    def test_default_timestamp_from_created_at(self):
        reset_adapter_cache()
        detections = convert_intelligence_to_detections(INTEL_SAMPLE)
        # 2026-06-03T01:32:42.390969Z → should be a reasonable Unix timestamp
        for d in detections:
            assert d.timestamp > 1_700_000_000  # after 2023
            assert d.timestamp < 1_800_000_000  # before 2027

    def test_explicit_timestamp(self):
        reset_adapter_cache()
        detections = convert_intelligence_to_detections(INTEL_SAMPLE, timestamp=1717400000.0)
        for d in detections:
            assert d.timestamp == 1717400000.0

    def test_speed_heading_from_consecutive_frames(self):
        """模拟同门多阶段数据：同一 track_id 连续两帧应算出非零速度。"""
        reset_adapter_cache()
        # 第一帧：track T-0001 在 (30.512, 114.381)
        frame1 = {
            **INTEL_SAMPLE,
            "targets": [INTEL_SAMPLE["targets"][0]],  # only T-0001
        }
        detections_1 = convert_intelligence_to_detections(frame1, timestamp=1000.0)
        assert detections_1[0].speed == 0.0  # first frame → no history

        # 第二帧：同一 track T-0001 向北移动约 0.01° ≈ 1110m，间隔 10s
        target_moved = {
            "track_id": "T-0001",
            "class": "bus",
            "label": "hostile",
            "affiliation": "red",
            "threat_level": "high",
            "geo": {"lat": 30.522, "lon": 114.381, "alt_m": 120.0},  # moved north
            "damage_score": None,
            "confidence": 0.99,
            "knowledge_ref": "ENT-T-0001",
        }
        frame2 = {
            **INTEL_SAMPLE,
            "targets": [target_moved],
        }
        detections_2 = convert_intelligence_to_detections(frame2, timestamp=1010.0)
        assert detections_2[0].speed > 50.0, f"Expected high speed for 0.01° in 10s, got {detections_2[0].speed}"
        # heading should be near 0° (north), with ±10° tolerance
        h = detections_2[0].heading
        assert min(abs(h), abs(360 - h)) < 10.0, f"Heading {h} not near 0° for pure north"


# ==================== Scene 提取测试 ====================

def test_extract_scene_from_intelligence():
    scene = extract_scene_from_intelligence(INTEL_SAMPLE)
    assert "operation_name" in scene
    assert scene["operation_name"] == "OP-IRON-VALLEY-2026"
    # 从 targets 几何中心推算的保护区
    assert "protected_zone_lat" in scene
    assert "protected_zone_lon" in scene
    assert "protected_radius_m" in scene
    assert scene["anti_jam_mode"] is True
    assert "provenance_summary" in scene


def test_extract_scene_with_override():
    override = {"protected_zone_lat": 31.0, "protected_zone_lon": 121.0, "protected_radius_m": 10000}
    scene = extract_scene_from_intelligence(INTEL_SAMPLE, override_scene=override)
    assert scene["protected_zone_lat"] == 31.0
    assert scene["protected_zone_lon"] == 121.0
    assert scene["protected_radius_m"] == 10000


# ==================== 集成测试：适配 → 构建 PerceptionResultRequest ====================

def test_adapted_detections_can_build_request():
    """验证适配后的 Detection 能被 Pydantic 正确校验（即字段类型完全兼容）。"""
    from app.models import Detection
    reset_adapter_cache()
    detections = convert_intelligence_to_detections(INTEL_SAMPLE)
    # 直接 model_validate 不应抛异常
    validated = [Detection.model_validate(d.model_dump()) for d in detections]
    assert len(validated) == 4


def test_adapted_detections_feed_tracker():
    """验证适配后的 Detection 能喂入 MultiTargetTracker。"""
    from app.tracker import MultiTargetTracker
    reset_adapter_cache()
    tracker = MultiTargetTracker()
    detections = convert_intelligence_to_detections(INTEL_SAMPLE)
    tracks = tracker.update(detections, algorithm_level="medium")
    # 4 个首次出现的 detection → 4 条新航迹
    assert len(tracks) == 4
    for track in tracks:
        assert track.track_id.startswith("trk-")


# ==================== 全局 reset 测试 ====================

def test_reset_adapter_cache():
    reset_adapter_cache()
    convert_intelligence_to_detections(INTEL_SAMPLE)
    # 缓存中应该有 4 个 track_id 的记录
    from app.intelligence_adapter import get_frame_cache
    cache = get_frame_cache()
    assert cache.get_previous("T-0001") is not None
    reset_adapter_cache()
    assert cache.get_previous("T-0001") is None

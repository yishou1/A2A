"""传感器输入与语义情报输出的数据契约。"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class SensorModality(str, Enum):
    EO_IR = "eo_ir"
    SAR = "sar"
    RADAR = "radar"
    ACOUSTIC = "acoustic"
    TEXT_REPORT = "text_report"
    TELEMETRY = "telemetry"


class SensorFrame(BaseModel):
    """单路传感器原始帧。"""

    sensor_id: str
    modality: SensorModality
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(
        description="原始数据：图像 base64、点云路径、雷达矩阵、文本等"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class SensorBatch(BaseModel):
    """前端上报的一批传感器数据。"""

    mission_id: str
    frames: list[SensorFrame]
    context: dict[str, Any] = Field(default_factory=dict)


# --- 技能中间结果 ---


class TargetGeo(BaseModel):
    """目标三维位置（感知层地理解算输出）。"""

    lat: float
    lon: float
    alt_m: float
    slant_range_m: float | None = None
    domain: str | None = None
    alt_source: str | None = None
    alt_confidence: float | None = None
    geo_method: str | None = None
    vertical_offset_m: float | None = None
    class_name: str | None = None


class Detection(BaseModel):
    track_id: str | None = None
    sensor_id: str | None = None
    class_name: str
    confidence: float
    bbox: list[float] | None = None
    geo: TargetGeo | dict[str, Any] | None = None
    damage_score: float | None = None
    epistemic_uncertainty: float | None = None


class SensorAssignment(BaseModel):
    sensor_id: str
    target_id: str | None = None
    task: str = "surveillance"
    priority: str = "normal"
    rationale: str = ""


class ReattackAssignment(BaseModel):
    asset_id: str
    target_id: str
    task: str = "reattack"
    priority: str = "critical"
    expected_damage: float | None = None
    rationale: str = ""


class TaskSchedulePlan(BaseModel):
    """MARL-PPO 传感器任务分配与重攻击规划。"""

    sensor_assignments: list[SensorAssignment] = Field(default_factory=list)
    reattack_plan: list[ReattackAssignment] = Field(default_factory=list)
    covered_targets: list[str] = Field(default_factory=list)
    reattack_targets: list[str] = Field(default_factory=list)
    algorithm: str = ""


class PerceptionOutput(BaseModel):
    detections: list[Detection] = Field(default_factory=list)
    tracks: list[dict[str, Any]] = Field(default_factory=list)
    verified_ids: list[str] = Field(default_factory=list)
    task_schedule: TaskSchedulePlan | None = None
    algorithm_trace: dict[str, str] = Field(default_factory=dict)
    algorithm_calls: list[dict[str, Any]] = Field(default_factory=list)
    algorithm_invocations: list[dict[str, Any]] = Field(default_factory=list)


class ThreatAssessment(BaseModel):
    target_id: str
    threat_level: str
    threat_score: float
    rationale: str = ""


class CognitionOutput(BaseModel):
    embeddings: dict[str, list[float]] = Field(default_factory=dict)
    classifications: list[dict[str, Any]] = Field(default_factory=list)
    threats: list[ThreatAssessment] = Field(default_factory=list)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    rag_context: str = ""
    algorithm_trace: dict[str, str] = Field(default_factory=dict)
    algorithm_calls: list[dict[str, Any]] = Field(default_factory=list)
    algorithm_invocations: list[dict[str, Any]] = Field(default_factory=list)


class SemanticIntelligencePacket(BaseModel):
    """供其他 Agent 消费的语义压缩情报（下游标准输入）。"""

    schema_version: str = Field(
        default="1.0",
        description="intelligence_packet 契约版本；下游按版本解析",
    )
    packet_id: str = Field(default_factory=lambda: str(uuid4()))
    mission_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    summary: str
    tracks: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "机器可读航迹层：含 object_type/lat/lon/speed/heading 与 history_path，"
            "供 trajectory_predictor / track_threat 等直接消费"
        ),
    )
    targets: list[dict[str, Any]] = Field(
        default_factory=list,
        description="语义目标层：威胁、敌我、类别，供决策/评估/火力使用",
    )
    semantic_vector: list[float] = Field(default_factory=list)
    knowledge_graph: dict[str, Any] = Field(default_factory=dict)
    routing: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    raw_compression_ratio: float = 1.0
    task_schedule: TaskSchedulePlan | None = None
    output_attachments: list[dict[str, Any]] = Field(
        default_factory=list,
        description="处理后产物（如标注图）的对象存储引用，供下游 Agent 通过 URI 读取",
    )
    consumer_guide: dict[str, Any] = Field(
        default_factory=dict,
        description="各字段应被哪些下游 Agent 消费的路由说明",
    )

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


class PerceptionOutput(BaseModel):
    detections: list[Detection] = Field(default_factory=list)
    tracks: list[dict[str, Any]] = Field(default_factory=list)
    verified_ids: list[str] = Field(default_factory=list)
    algorithm_trace: dict[str, str] = Field(default_factory=dict)


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


class SemanticIntelligencePacket(BaseModel):
    """供其他 Agent 消费的语义压缩情报。"""

    packet_id: str = Field(default_factory=lambda: str(uuid4()))
    mission_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    summary: str
    targets: list[dict[str, Any]] = Field(default_factory=list)
    semantic_vector: list[float] = Field(default_factory=list)
    knowledge_graph: dict[str, Any] = Field(default_factory=dict)
    routing: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    raw_compression_ratio: float = 1.0
    output_attachments: list[dict[str, Any]] = Field(
        default_factory=list,
        description="处理后产物（如标注图）的对象存储引用，供下游 Agent 通过 URI 读取",
    )

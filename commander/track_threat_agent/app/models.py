"""Pydantic models for the simulation-only track and risk demo."""

from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ObjectType = Literal["aircraft", "ship", "uav", "unknown"]
ThreatLevel = Literal["low", "medium", "high"]
GroupType = Literal["air_formation", "surface_group", "mixed_group", "unknown_group"]
ProtectedAssetType = Literal[
    "command_post",
    "radar_site",
    "logistics_node",
    "harbor_facility",
    "harbor",
    "airport",
    "convoy",
    "communication_node",
    "medical_node",
    "civil_infrastructure",
]


class Detection(BaseModel):
    detection_id: str
    object_type: ObjectType = "unknown"
    timestamp: float
    lat: float
    lon: float
    alt: float = 0.0
    speed: float = 0.0
    heading: float = 0.0
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_agent: str = "simulator"
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("heading")
    @classmethod
    def normalize_heading(cls, value: float) -> float:
        return value % 360.0


class TrackState(BaseModel):
    track_id: str
    object_type: ObjectType = "unknown"
    lat: float
    lon: float
    alt: float = 0.0
    speed: float = 0.0
    heading: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    track_quality: float = Field(default=1.0, ge=0.0, le=1.0)
    last_update_time: float
    missed_count: int = 0
    history_path: List[Dict[str, float]] = Field(default_factory=list)
    predicted_path: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("history_path")
    @classmethod
    def trim_history_path(cls, value: List[Dict[str, float]]) -> List[Dict[str, float]]:
        return value[-50:]


class ThreatAssessment(BaseModel):
    threat_id: str
    track_id: str
    score: float = Field(ge=0.0, le=1.0)
    level: ThreatLevel
    rank: int
    factors: Dict[str, float] = Field(default_factory=dict)
    evidence: List[str] = Field(default_factory=list)
    timestamp: float
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TrackGroup(BaseModel):
    group_id: str
    group_type: GroupType
    member_track_ids: List[str]
    centroid: Dict[str, float]
    centroid_prediction: List[Dict[str, Any]] = Field(default_factory=list)
    envelope: Dict[str, float]
    predicted_envelope: Dict[str, float]
    cohesion_score: float = Field(ge=0.0, le=1.0)
    group_threat_score: float = Field(ge=0.0, le=1.0)
    group_threat_level: ThreatLevel
    evidence: List[str] = Field(default_factory=list)
    timestamp: float
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ProtectedAsset(BaseModel):
    asset_id: str
    asset_name: str
    asset_type: ProtectedAssetType = "civil_infrastructure"
    lat: float
    lon: float
    alt: float = 0.0
    protection_radius_m: float = Field(default=5_000.0, gt=0.0)
    criticality: float = Field(default=0.7, ge=0.0, le=1.0)
    priority: float | None = Field(default=None, ge=0.0, le=1.0)
    vulnerability: float = Field(default=0.5, ge=0.0, le=1.0)
    status: str = "protected"
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def align_priority_and_criticality(self) -> "ProtectedAsset":
        if self.priority is None:
            self.priority = self.criticality
        return self


class AssetImpactAssessment(BaseModel):
    impact_id: str
    protected_asset_id: str
    protected_asset_name: str
    protected_asset_type: ProtectedAssetType
    source_track_id: str
    source_threat_id: str | None = None
    source_object_type: ObjectType
    score: float = Field(ge=0.0, le=1.0)
    level: ThreatLevel
    rank: int
    closest_distance_m: float
    predicted_closest_distance_m: float
    predicted_min_distance_margin_m: float = 0.0
    closest_time_s: float | None = None
    eta_to_protected_radius_s: float | None = None
    will_enter_protection_radius: bool = False
    factors: Dict[str, float] = Field(default_factory=dict)
    evidence: List[str] = Field(default_factory=list)
    timestamp: float
    metadata: Dict[str, Any] = Field(default_factory=dict)

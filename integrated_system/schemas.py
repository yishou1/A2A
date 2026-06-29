from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class Contact(BaseModel):
    contact_id: str
    kind: str = "unknown"
    location: str = "unknown"
    threat_level: float = Field(default=0.5, ge=0.0, le=1.0)
    velocity: Optional[float] = None
    intent: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FriendlyPlatform(BaseModel):
    platform_id: str
    platform_type: str = "generic"
    readiness: float = Field(default=0.8, ge=0.0, le=1.0)
    munitions: int = Field(default=1, ge=0)
    location: str = "unknown"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class IntegratedMissionRequest(BaseModel):
    objective: str
    mission_type: str = "integrated_demo"
    scenario_name: str = "demo-scenario"
    contacts: List[Contact] = Field(default_factory=list)
    friendly_platforms: List[FriendlyPlatform] = Field(default_factory=list)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    environment: Dict[str, Any] = Field(default_factory=dict)
    scene: Dict[str, Any] = Field(default_factory=dict)
    protected_assets: List[Dict[str, Any]] = Field(default_factory=list)
    perception_frames: List[Dict[str, Any]] = Field(default_factory=list)
    intelligence_text: Optional[str] = None
    success_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    max_replans: int = Field(default=1, ge=0, le=3)
    demo_delay_ms: int = Field(default=0, ge=0, le=5000)
    require_operator_approval: bool = False
    approval_override: Optional[bool] = None
    simulation_mode: Literal["safe", "sandbox"] = "safe"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MissionAdjustmentRequest(BaseModel):
    note: Optional[str] = None
    success_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    additional_constraint: Optional[str] = None
    approval_override: Optional[bool] = None
    planning_focus: Optional[str] = None


class MissionControlRequest(BaseModel):
    action: Literal["pause", "resume", "abort"]

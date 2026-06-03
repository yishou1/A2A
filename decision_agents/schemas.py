"""Shared request and response schemas for the three agent families."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


ComputeBudget = Literal["small", "medium", "large"]
RiskPolicy = Literal["conservative", "balanced"]
AgentName = Literal[
    "track_threat_agent",
    "decision_planning_agent",
    "compliance_authorization_agent",
]
ResponseStatus = Literal["completed", "input_required", "error"]
RiskLevel = Literal["high", "medium", "low"]
ResourceStatus = Literal["available", "busy", "offline", "unknown"]
PlanStatus = Literal["candidate", "recommended", "rejected"]
AuthorizationStatus = Literal[
    "approved",
    "pending_review",
    "denied",
    "expired",
    "unknown",
]
ComplianceDecision = Literal["approved", "blocked", "review_required"]
RuleSeverity = Literal["info", "warning", "blocking"]


class AgentProfile(BaseModel):
    compute_budget: ComputeBudget = "small"
    risk_policy: RiskPolicy = "balanced"


class Observation(BaseModel):
    id: str
    timestamp: str | int | float
    x: float | None = None
    y: float | None = None
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    altitude: float | None = None
    source_format: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    target_hint: str | None = None
    sensor_id: str | None = None
    source_reliability: float = Field(default=1.0, ge=0.0, le=1.0)
    object_type: str = "unknown"
    speed_hint: float | None = Field(default=None, ge=0.0)
    heading_hint: float | None = Field(default=None, ge=0.0, le=360.0)
    features: dict[str, Any] = Field(default_factory=dict)


class Track(BaseModel):
    id: str
    source_observations: list[str] = Field(default_factory=list)
    object_type: str = "unknown"
    start_time: str | None = None
    end_time: str | None = None
    last_position: dict[str, float] = Field(default_factory=dict)
    velocity: dict[str, float] = Field(default_factory=dict)
    speed: float = Field(default=0.0, ge=0.0)
    heading: float = Field(default=0.0, ge=0.0, le=360.0)
    trend: str = "insufficient observations"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class RiskAssessment(BaseModel):
    track_id: str
    priority: int = Field(ge=1)
    risk: RiskLevel
    threat_score: float = Field(ge=0.0, le=100.0)
    probability: float = Field(ge=0.0, le=1.0)
    rationale: str
    triggered_rules: list[str] = Field(default_factory=list)


class ScheduledTask(BaseModel):
    id: str
    target_id: str | None = None
    priority: int = Field(default=1, ge=1)
    task_type: str = "monitor"
    deadline: str | None = None
    required_resource_types: list[str] = Field(default_factory=list)


class Resource(BaseModel):
    id: str
    type: str
    status: ResourceStatus = "unknown"
    capacity: float = Field(default=1.0, ge=0.0)
    location: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class CandidatePlan(BaseModel):
    id: str
    name: str
    status: PlanStatus = "candidate"
    target_ids: list[str] = Field(default_factory=list)
    assigned_resources: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    expected_effects: list[str] = Field(default_factory=list)
    score: float = Field(default=0.0, ge=0.0, le=100.0)
    rationale: str = ""
    assumptions: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


class AuthorizationState(BaseModel):
    status: AuthorizationStatus = "unknown"
    approver: str | None = None
    approval_level: str | None = None
    scope: list[str] = Field(default_factory=list)
    approved_plan_ids: list[str] = Field(default_factory=list)
    expires_at: str | None = None
    notes: list[str] = Field(default_factory=list)


class RuleEvidence(BaseModel):
    source: str
    rule_id: str
    title: str
    text: str
    score: float = Field(default=0.0, ge=0.0)
    tags: list[str] = Field(default_factory=list)


class RuleViolation(BaseModel):
    rule_id: str
    severity: RuleSeverity
    item: str
    message: str
    suggestion: str = ""
    evidence_rule_ids: list[str] = Field(default_factory=list)


class PlanComplianceResult(BaseModel):
    plan_id: str
    plan_status: PlanStatus
    decision: ComplianceDecision
    approved_for_demo_handoff: bool
    requires_human_approval: bool
    violations: list[RuleViolation] = Field(default_factory=list)
    blocked_items: list[str] = Field(default_factory=list)
    evidence: list[RuleEvidence] = Field(default_factory=list)
    adjustment_suggestions: list[str] = Field(default_factory=list)


class ComplianceCheckResult(PlanComplianceResult):
    selected_plan_id: str
    authorization_status: AuthorizationState = Field(default_factory=AuthorizationState)
    per_plan_results: list[PlanComplianceResult] = Field(default_factory=list)


class AgentRequest(BaseModel):
    request_id: str = "demo-001"
    agent_profile: AgentProfile = Field(default_factory=AgentProfile)
    algorithm_id: str | None = None
    algorithm_params: dict[str, Any] = Field(default_factory=dict)
    observations: list[Observation] = Field(default_factory=list)
    tracks: list[Track] = Field(default_factory=list)
    risk_assessments: list[RiskAssessment] = Field(default_factory=list)
    scheduled_tasks: list[ScheduledTask] = Field(default_factory=list)
    resources: list[Resource] = Field(default_factory=list)
    candidate_plans: list[CandidatePlan] = Field(default_factory=list)
    constraints: list[dict[str, Any] | str] = Field(default_factory=list)
    authorization: AuthorizationState = Field(default_factory=AuthorizationState)

    @field_validator("observations")
    @classmethod
    def unique_observation_ids(cls, observations: list[Observation]) -> list[Observation]:
        ids = [observation.id for observation in observations]
        if len(ids) != len(set(ids)):
            raise ValueError("observation ids must be unique")
        return observations


class AgentResponse(BaseModel):
    status: ResponseStatus = "completed"
    agent: AgentName
    selected_algorithms: list[str] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)
    rag_evidence: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    warnings: list[str] = Field(default_factory=list)

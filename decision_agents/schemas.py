"""Shared request and response schemas for the planning and rule agents."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ComputeBudget = Literal["small", "medium", "large"]
RiskPolicy = Literal["conservative", "balanced"]
AgentName = Literal[
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


class RiskAssessment(BaseModel):
    target_id: str
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


class TargetHistoryStep(BaseModel):
    timestamp: str | None = None
    risk_score: float = Field(default=50.0, ge=0.0, le=100.0)
    probability: float = Field(default=0.5, ge=0.0, le=1.0)
    priority: int = Field(default=1, ge=1)
    resource_pressure: float = Field(default=0.5, ge=0.0, le=1.0)


class TargetHistory(BaseModel):
    target_id: str
    steps: list[TargetHistoryStep] = Field(default_factory=list)


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
    doc_id: str | None = None
    doc_type: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    section: str | None = None
    chunk_id: str | None = None
    citation: str | None = None
    content_hash: str | None = None


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
    risk_assessments: list[RiskAssessment] = Field(default_factory=list)
    scheduled_tasks: list[ScheduledTask] = Field(default_factory=list)
    resources: list[Resource] = Field(default_factory=list)
    target_histories: list[TargetHistory] = Field(default_factory=list)
    planning_objectives: list[str] = Field(default_factory=list)
    candidate_plans: list[CandidatePlan] = Field(default_factory=list)
    constraints: list[dict[str, Any] | str] = Field(default_factory=list)
    authorization: AuthorizationState = Field(default_factory=AuthorizationState)

class AgentResponse(BaseModel):
    status: ResponseStatus = "completed"
    agent: AgentName
    selected_algorithms: list[str] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)
    rag_evidence: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    warnings: list[str] = Field(default_factory=list)

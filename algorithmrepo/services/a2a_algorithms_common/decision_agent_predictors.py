"""Compose decision-support capabilities behind the two core services."""

from __future__ import annotations

from typing import Any

from decision_support.compliance import execute_medium_compliance_authorization
from decision_support.planning import execute_medium_decision_planning
from decision_support.schemas import AgentRequest

from .decision_capabilities import (
    OnnxComplianceCapabilities,
    OnnxPlanningCapabilities,
)


def predict_decision_planning_core(
    inputs: dict[str, Any], params: dict[str, Any]
) -> dict[str, Any]:
    """Run planning with independently packaged model capabilities."""
    del params
    request = AgentRequest.model_validate(inputs)
    if not request.scheduled_tasks:
        raise ValueError("scheduled_tasks is required.")
    if not request.resources:
        raise ValueError("resources is required.")
    return execute_medium_decision_planning(request, OnnxPlanningCapabilities())


def predict_compliance_authorization_core(
    inputs: dict[str, Any], params: dict[str, Any]
) -> dict[str, Any]:
    """Run compliance checks with an independent risk capability."""
    del params
    request = AgentRequest.model_validate(inputs)
    if not request.candidate_plans:
        raise ValueError("candidate_plans is required.")
    return execute_medium_compliance_authorization(
        request, OnnxComplianceCapabilities()
    )

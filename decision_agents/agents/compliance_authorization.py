"""Compliance and authorization agent."""

from __future__ import annotations

from decision_agents.agents.base import AlgorithmAgent
from decision_agents.algorithms.compliance_authorization import (
    run_compliance_authorization,
)
from decision_agents.schemas import AgentRequest, AgentResponse


class ComplianceAuthorizationAgent(AlgorithmAgent):
    agent_name = "compliance_authorization_agent"

    def run(self, request: AgentRequest) -> AgentResponse:
        return run_compliance_authorization(request)


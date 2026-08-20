"""Compliance and authorization agent."""

from __future__ import annotations

from decision_agents.common.algolib_runtime import run_agent_with_algolib, use_algolib_backend
from decision_agents.common.base_agent import AlgorithmAgent
from decision_agents.common.schemas import AgentRequest, AgentResponse
from decision_agents.compliance_authorization.local_algorithm import (
    run_compliance_authorization,
)


class ComplianceAuthorizationAgent(AlgorithmAgent):
    agent_name = "compliance_authorization_agent"

    def run(self, request: AgentRequest) -> AgentResponse:
        if use_algolib_backend():
            return run_agent_with_algolib(self.agent_name, request)
        return run_compliance_authorization(request)

"""Decision planning agent."""

from __future__ import annotations

from decision_agents.agents.base import AlgorithmAgent
from decision_agents.algorithms.decision_planning import run_decision_planning
from decision_agents.schemas import AgentRequest, AgentResponse


class DecisionPlanningAgent(AlgorithmAgent):
    agent_name = "decision_planning_agent"

    def run(self, request: AgentRequest) -> AgentResponse:
        return run_decision_planning(request)


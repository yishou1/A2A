"""Track and threat assessment agent."""

from __future__ import annotations

from decision_agents.agents.base import AlgorithmAgent
from decision_agents.algorithms.track_threat import run_track_threat
from decision_agents.schemas import AgentRequest, AgentResponse


class TrackThreatAgent(AlgorithmAgent):
    agent_name = "track_threat_agent"

    def run(self, request: AgentRequest) -> AgentResponse:
        return run_track_threat(request)


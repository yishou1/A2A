"""A2A server adapter for deterministic decision-agent algorithms."""

from __future__ import annotations

from a2a_protocol.server import A2ABaseAgent
from decision_agents.a2a_payloads import (
    agent_response_to_a2a_response,
    agent_response_to_output,
    run_agent_payload,
)


class DecisionAlgorithmA2AAgent(A2ABaseAgent):
    def __init__(self, *, algorithm_agent, name: str, description: str, role: str, port: int):
        self.algorithm_agent = algorithm_agent
        super().__init__(name=name, description=description, role=role, port=port)

    def get_agent_card(self):
        card = super().get_agent_card()
        card["skills"] = [
            {
                "id": f"{self.role}_analysis",
                "name": self.name,
                "description": self.description,
                "tags": ["project-613", "decision-support", self.role],
            }
        ]
        return card

    def handle_message(self, payload, token):
        del token
        response = self._run_algorithm(payload)
        return agent_response_to_a2a_response(
            payload=payload,
            response=response,
            agent_name=self.algorithm_agent.agent_name,
            work_list_size=len(self.get_work_list(payload.get("workflow_id"))),
        )

    def execute_task(self, payload):
        response = self._run_algorithm(payload)
        if response.status != "completed":
            raise RuntimeError(response.summary or response.status)
        return agent_response_to_output(response), response.summary

    def _run_algorithm(self, payload):
        return run_agent_payload(
            self.algorithm_agent,
            self.algorithm_agent.agent_name,
            payload,
        )

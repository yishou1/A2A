"""A2A server adapter for deterministic decision-agent algorithms."""

from __future__ import annotations

from a2a_protocol.server import A2ABaseAgent
from decision_agents.common.a2a_payloads import (
    agent_response_to_a2a_response,
    agent_response_to_output,
    run_agent_payload,
)
from decision_agents.common.definitions import agent_definition_for_role


class A2ATaskResponseError(RuntimeError):
    """Carry a fully formed task response through the main A2A server error path."""

    def __init__(self, task_response):
        super().__init__(task_response.get("message") or task_response.get("error") or "agent task failed")
        self.task_response = task_response
        self.code = task_response.get("error_code") or "AGENT_BUSINESS_ERROR"


class DecisionAlgorithmA2AAgent(A2ABaseAgent):
    def __init__(
        self,
        *,
        algorithm_agent,
        name: str,
        description: str,
        role: str,
        port: int,
        idempotency_db_path: str | None = None,
        max_concurrent_tasks: int | None = None,
    ):
        self.algorithm_agent = algorithm_agent
        definition = agent_definition_for_role(role)
        skill_id = definition["skill_id"] if definition else f"{role}_analysis"
        skill_name = definition["skill_name"] if definition else name
        super().__init__(
            name=name,
            description=description,
            role=role,
            port=port,
            idempotency_db_path=idempotency_db_path,
            max_concurrent_tasks=max_concurrent_tasks,
            skills=[
                {
                    "id": skill_id,
                    "name": skill_name,
                    "description": description,
                    "tags": ["project-613", "decision-support", role],
                }
            ],
        )

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
            raise A2ATaskResponseError(
                agent_response_to_a2a_response(
                    payload=payload,
                    response=response,
                    agent_name=self.algorithm_agent.agent_name,
                    work_list_size=len(self.get_work_list(payload.get("workflow_id"))),
                )
            )
        return agent_response_to_output(response), response.summary

    def _run_algorithm(self, payload):
        return run_agent_payload(
            self.algorithm_agent,
            self.algorithm_agent.agent_name,
            payload,
        )

import json
import time
from copy import deepcopy
from typing import Dict, Iterable, Tuple

from decision_agents.a2a_payloads import agent_response_to_a2a_response, run_agent_payload
from decision_agents.agents import (
    ComplianceAuthorizationAgent,
    DecisionPlanningAgent,
)


class LocalAgentRuntime:
    """
    Local in-process runtime for Commander workflow debugging.

    It mirrors the A2A discovery/auth/send flow without Nacos, HTTP, or uvicorn.
    This is useful when you want to validate workflow branching locally before
    starting the full distributed stack.
    """

    def __init__(self):
        self._task_response_cache = {}
        self._stream_response_cache = {}
        self._workflow_work_lists = {}
        self._algorithm_agents = {
            "decision_planning": DecisionPlanningAgent(),
            "compliance_authorization": ComplianceAuthorizationAgent(),
        }

    AGENTS = {
        "recon": {
            "name": "Local_Recon_Agent",
            "description": "Local reconnaissance unit.",
            "role": "recon",
        },
        "artillery": {
            "name": "Local_Artillery_Agent",
            "description": "Local artillery simulation unit.",
            "role": "artillery",
        },
        "evaluator": {
            "name": "Local_Evaluator_Agent",
            "description": "Local strike evaluation unit.",
            "role": "evaluator",
        },
        "assault": {
            "name": "Local_Assault_Agent",
            "description": "Local assault unit.",
            "role": "assault",
        },
        "decision_planning": {
            "name": "Local_Decision_Planning_Agent",
            "description": "Local decision planning unit.",
            "role": "decision_planning",
        },
        "compliance_authorization": {
            "name": "Local_Compliance_Authorization_Agent",
            "description": "Local compliance and authorization unit.",
            "role": "compliance_authorization",
        },
    }

    def discover(self, role: str) -> Dict[str, str]:
        if role not in self.AGENTS:
            raise ValueError(f"Unsupported local role: {role}")
        return dict(self.AGENTS[role])

    @staticmethod
    def _work_item_from_payload(payload: dict) -> str:
        return payload.get("work_item") or payload.get("task_id", "work-item-001")

    def _capture_work_list(self, payload: dict) -> None:
        workflow_id = payload.get("workflow_id")
        work_list = payload.get("work_list")
        if workflow_id and isinstance(work_list, list):
            self._workflow_work_lists[workflow_id] = deepcopy(work_list)

    def get_work_list(self, workflow_id: str) -> list[dict]:
        return deepcopy(self._workflow_work_lists.get(workflow_id, []))

    def authenticate(self, role: str) -> str:
        self.discover(role)
        return f"local-token-{role}"

    def send_message(self, role: str, payload: dict) -> dict:
        self.discover(role)
        self._capture_work_list(payload)
        work_item = self._work_item_from_payload(payload)
        if work_item in self._task_response_cache:
            return self._task_response_cache[work_item]

        if role in self._algorithm_agents:
            agent = self._algorithm_agents[role]
            algorithm_response = run_agent_payload(agent, agent.agent_name, payload)
            response = agent_response_to_a2a_response(
                payload=payload,
                response=algorithm_response,
                agent_name=agent.agent_name,
                work_list_size=len(self.get_work_list(payload.get("workflow_id"))),
            )
            response["mode"] = "local"
            self._task_response_cache[work_item] = response
            return response

        response = {
            "work_item": work_item,
            "workflow_id": payload.get("workflow_id"),
            "status": "Accepted",
            "mode": "local",
            "role": role,
            "message": self._message_for(role, payload),
            "work_list_size": len(self.get_work_list(payload.get("workflow_id"))),
        }
        self._task_response_cache[work_item] = response
        return response

    def send_message_stream(self, role: str, payload: dict) -> Iterable[dict]:
        self.discover(role)
        self._capture_work_list(payload)
        work_item = self._work_item_from_payload(payload)
        cached_events = self._stream_response_cache.get(work_item)
        if cached_events is not None:
            for event in cached_events:
                yield event
            return

        if role != "artillery":
            events = [{
                "status": "Completed",
                "progress": "100%",
                "message": self._message_for(role, payload),
            }]
            self._stream_response_cache[work_item] = events
            yield from events
            return

        events = [
            {"status": "Working", "progress": "10%", "message": "Target locked"},
            {"status": "Working", "progress": "30%", "message": "Firing Volley 1"},
            {"status": "Working", "progress": "60%", "message": "Impact confirmed. Adjusting aim."},
            {"status": "Completed", "progress": "100%", "message": "Target suppression complete"},
        ]
        self._stream_response_cache[work_item] = events
        for event in events:
            time.sleep(0.1)
            yield event

    @staticmethod
    def encode_stream_event(event: dict) -> str:
        return json.dumps(event, ensure_ascii=False)

    def execute(self, role: str, payload: dict, stream: bool = False) -> Tuple[dict, list]:
        card = self.discover(role)
        token = self.authenticate(role)
        events = []
        if stream:
            events = list(self.send_message_stream(role, payload))
            response = {
                "status": "Completed" if events and events[-1].get("status") == "Completed" else "Accepted",
                "mode": "local",
                "role": role,
                "token": token,
            }
        else:
            response = self.send_message(role, payload)
            response["token"] = token
        response["agent_card"] = card
        return response, events

    @staticmethod
    def _message_for(role: str, payload: dict) -> str:
        command = payload.get("command", "")
        if role == "recon":
            return f"Local recon completed command={command}"
        if role == "artillery":
            return f"Local artillery completed command={command}"
        if role == "evaluator":
            return f"Local evaluator completed command={command}"
        if role == "assault":
            return f"Local assault completed command={command}"
        return f"Local agent completed command={command}"

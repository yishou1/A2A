import json
import time
from copy import deepcopy
from typing import Dict, Iterable, Tuple

from a2a_protocol.messages import build_task_response


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

        output, message = self._output_for(role, payload)
        response = build_task_response(
            workflow_id=payload.get("workflow_id"),
            work_item=work_item,
            agent=self.AGENTS[role]["name"],
            role=role,
            command=payload.get("command"),
            status="completed",
            output=output,
            metrics={"latency_ms": 0.0},
            message=message,
            work_list_size=len(self.get_work_list(payload.get("workflow_id"))),
            extra={"mode": "local"},
        )
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
            output, message = self._output_for(role, payload)
            response = build_task_response(
                workflow_id=payload.get("workflow_id"),
                work_item=self._work_item_from_payload(payload),
                agent=card["name"],
                role=role,
                command=payload.get("command"),
                status="completed" if events and events[-1].get("status") == "Completed" else "accepted",
                output=output,
                metrics={"stream_events": len(events)},
                message=message,
                work_list_size=len(self.get_work_list(payload.get("workflow_id"))),
                extra={"mode": "local", "token": token},
            )
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

    def _output_for(self, role: str, payload: dict) -> tuple[dict, str]:
        output_hint = payload.get("output_hint") or "result"
        message = self._message_for(role, payload)

        if role == "recon":
            value = "Sector_A is heavily fortified with overlapping machine gun nests."
        elif role == "artillery":
            value = "Suppression barrage executed on Sector_A."
        elif role == "evaluator":
            value = int(payload.get("input", {}).get("mock_eval_score", 40))
        elif role == "assault":
            value = "Assault unit captured the beachhead."
        else:
            value = message
        return {output_hint: value}, message

import json
import time
from typing import Dict, Iterable, Tuple


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
    def _task_id_from_payload(payload: dict) -> str:
        return payload.get("task_id", "t-001")

    def authenticate(self, role: str) -> str:
        self.discover(role)
        return f"local-token-{role}"

    def send_message(self, role: str, payload: dict) -> dict:
        self.discover(role)
        task_id = self._task_id_from_payload(payload)
        if task_id in self._task_response_cache:
            return self._task_response_cache[task_id]

        response = {
            "task_id": task_id,
            "status": "Accepted",
            "mode": "local",
            "role": role,
            "message": self._message_for(role, payload),
        }
        self._task_response_cache[task_id] = response
        return response

    def send_message_stream(self, role: str, payload: dict) -> Iterable[dict]:
        self.discover(role)
        task_id = self._task_id_from_payload(payload)
        cached_events = self._stream_response_cache.get(task_id)
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
            self._stream_response_cache[task_id] = events
            yield from events
            return

        events = [
            {"status": "Working", "progress": "10%", "message": "Target locked"},
            {"status": "Working", "progress": "30%", "message": "Firing Volley 1"},
            {"status": "Working", "progress": "60%", "message": "Impact confirmed. Adjusting aim."},
            {"status": "Completed", "progress": "100%", "message": "Target suppression complete"},
        ]
        self._stream_response_cache[task_id] = events
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

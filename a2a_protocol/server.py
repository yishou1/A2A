from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.responses import StreamingResponse
import uvicorn
import asyncio
import json
import os
import threading
import time
from copy import deepcopy
from urllib.parse import urljoin

from a2a_protocol.messages import build_task_error_response, build_task_response

def verify_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return authorization.split("Bearer ")[1]

class A2ABaseAgent:
    def __init__(self, name: str, description: str, role: str, port: int):
        self.name = name
        self.description = description
        self.role = role
        self.port = port
        self.started_at = time.time()
        self.ready = True
        self._task_response_cache = {}
        self._stream_response_cache = {}
        self._workflow_work_lists = {}
        self._metrics = {
            "tasks_received": 0,
            "tasks_completed": 0,
            "tasks_failed": 0,
            "stream_requests": 0,
            "cache_hits": 0,
            "active_tasks": 0,
            "last_error": None,
            "last_work_item": None,
        }
        self._state_lock = threading.RLock()
        self.app = FastAPI(title=name)
        self.setup_routes()

    def get_agent_card(self):
        auth_server_base = os.environ.get("A2A_AUTH_SERVER_BASE", "http://127.0.0.1:8080")
        auth_server_base = auth_server_base.rstrip("/") + "/"
        return {
            "name": self.name,
            "description": self.description,
            "role": self.role,
            "securitySchemes": {
                "openIdConnect": {
                    "type": "openIdConnect",
                    "authorizationUrl": urljoin(auth_server_base, "auth"),
                    "tokenUrl": urljoin(auth_server_base, "post")
                }
            },
            "sendMessageEndpoint": "/sendMessage",
            "sendMessageStreamEndpoint": "/sendMessageStream",
            "workListEndpoint": "/workflows/{workflow_id}/work-list",
            "healthEndpoint": "/health",
            "readyEndpoint": "/ready",
            "metricsEndpoint": "/metrics",
        }

    def execute_task(self, payload):
        output_hint = payload.get("output_hint") or "result"
        message = f"{self.name} completed command={payload.get('command')}"
        output = {output_hint: self._default_output_value(output_hint, payload)}
        return output, message

    def _default_output_value(self, output_hint, payload):
        if output_hint == "recon_report":
            sector = payload.get("input", {}).get("sector", "unknown sector")
            return f"{sector} is heavily fortified with overlapping defensive positions."
        if output_hint == "strike_result":
            coordinates = payload.get("input", {}).get("coordinates", "unknown coordinates")
            return f"Suppression barrage executed on {coordinates}."
        if output_hint == "eval_score":
            return int(payload.get("input", {}).get("mock_eval_score", 40))
        if output_hint == "assault_result":
            coordinates = payload.get("input", {}).get("coordinates", "unknown coordinates")
            return f"Assault unit captured objective at {coordinates}."
        return f"{self.name} completed {payload.get('command')}"

    async def execute_stream(self, payload):
        # 默认的流式状态汇报
        yield f"data: {json.dumps({'status': 'Working', 'message': f'{self.name} processing stream'})}\n\n"
        await asyncio.sleep(1)
        yield f"data: {json.dumps({'status': 'Completed', 'message': 'Done'})}\n\n"

    def _work_item_from_payload(self, payload):
        return payload.get("work_item") or payload.get("task_id", "work-item-001")

    def _capture_work_list(self, payload):
        workflow_id = payload.get("workflow_id")
        work_list = payload.get("work_list")
        if workflow_id and isinstance(work_list, list):
            with self._state_lock:
                self._workflow_work_lists[workflow_id] = deepcopy(work_list)

    def get_work_list(self, workflow_id):
        with self._state_lock:
            return deepcopy(self._workflow_work_lists.get(workflow_id, []))

    def metrics_snapshot(self):
        with self._state_lock:
            snapshot = deepcopy(self._metrics)
        snapshot.update(
            {
                "agent": self.name,
                "role": self.role,
                "port": self.port,
                "ready": self.ready,
                "uptime_seconds": round(time.time() - self.started_at, 3),
            }
        )
        return snapshot

    async def _replay_stream(self, cached_events):
        for event in cached_events:
            yield event

    async def _cached_stream(self, payload):
        self._capture_work_list(payload)
        work_item = self._work_item_from_payload(payload)
        with self._state_lock:
            cached_events = self._stream_response_cache.get(work_item)
        if cached_events is not None:
            async for event in self._replay_stream(cached_events):
                yield event
            return

        buffered_events = []
        async for event in self.execute_stream(payload):
            buffered_events.append(event)
            yield event
        with self._state_lock:
            self._stream_response_cache[work_item] = buffered_events

    def setup_routes(self):
        @self.app.get("/health")
        async def health():
            return {
                "status": "ok",
                "agent": self.name,
                "role": self.role,
                "uptime_seconds": round(time.time() - self.started_at, 3),
            }

        @self.app.get("/ready")
        async def ready():
            with self._state_lock:
                active_tasks = self._metrics["active_tasks"]
            return {
                "ready": self.ready,
                "agent": self.name,
                "role": self.role,
                "active_tasks": active_tasks,
            }

        @self.app.post("/lifecycle/ready")
        async def set_ready(payload: dict):
            self.ready = bool(payload.get("ready", True))
            return {
                "ready": self.ready,
                "agent": self.name,
                "role": self.role,
            }

        @self.app.get("/metrics")
        async def metrics():
            return self.metrics_snapshot()

        @self.app.get("/.well-known/agent-card")
        async def agent_card():
            return self.get_agent_card()

        @self.app.get("/workflows/{workflow_id}/work-list")
        async def workflow_work_list(workflow_id: str):
            return {
                "workflow_id": workflow_id,
                "agent": self.name,
                "role": self.role,
                "work_list": self.get_work_list(workflow_id),
            }

        @self.app.post("/sendMessage")
        async def send_message(payload: dict, token: str = Depends(verify_token)):
            if not self.ready:
                work_item = self._work_item_from_payload(payload)
                return build_task_error_response(
                    workflow_id=payload.get("workflow_id"),
                    work_item=work_item,
                    agent=self.name,
                    role=self.role,
                    command=payload.get("command"),
                    error="agent is not ready",
                )
            self._capture_work_list(payload)
            work_item = self._work_item_from_payload(payload)
            with self._state_lock:
                cached_response = self._task_response_cache.get(work_item)
            if cached_response is not None:
                with self._state_lock:
                    self._metrics["cache_hits"] += 1
                cached = deepcopy(cached_response)
                cached["cached"] = True
                return cached

            started = time.perf_counter()
            with self._state_lock:
                self._metrics["tasks_received"] += 1
                self._metrics["active_tasks"] += 1
                self._metrics["last_work_item"] = work_item
            try:
                output, message = self.execute_task(payload)
                duration_ms = round((time.perf_counter() - started) * 1000, 3)
                response = build_task_response(
                    workflow_id=payload.get("workflow_id"),
                    work_item=work_item,
                    agent=self.name,
                    role=self.role,
                    command=payload.get("command"),
                    status="completed",
                    output=output,
                    metrics={
                        "latency_ms": duration_ms,
                        "duration_ms": duration_ms,
                    },
                    message=message,
                    work_list_size=len(self.get_work_list(payload.get("workflow_id"))),
                )
                with self._state_lock:
                    self._metrics["tasks_completed"] += 1
                    self._task_response_cache[work_item] = response
                return response
            except Exception as exc:
                duration_ms = round((time.perf_counter() - started) * 1000, 3)
                response = build_task_error_response(
                    workflow_id=payload.get("workflow_id"),
                    work_item=work_item,
                    agent=self.name,
                    role=self.role,
                    command=payload.get("command"),
                    error=str(exc),
                    metrics={
                        "latency_ms": duration_ms,
                        "duration_ms": duration_ms,
                    },
                )
                with self._state_lock:
                    self._metrics["tasks_failed"] += 1
                    self._metrics["last_error"] = str(exc)
                    self._task_response_cache[work_item] = response
                return response
            finally:
                with self._state_lock:
                    self._metrics["active_tasks"] = max(0, self._metrics["active_tasks"] - 1)
        
        @self.app.post("/sendMessageStream")
        async def send_message_stream(payload: dict, token: str = Depends(verify_token)):
            if not self.ready:
                raise HTTPException(status_code=503, detail="agent is not ready")
            with self._state_lock:
                self._metrics["stream_requests"] += 1
            return StreamingResponse(self._cached_stream(payload), media_type="text/event-stream")

    def start(self):
        uvicorn.run(self.app, host="0.0.0.0", port=self.port)

from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn
import asyncio
import json
import os
import threading
from copy import deepcopy
from urllib.parse import urljoin

async def verify_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return authorization.split("Bearer ")[1]

class A2ABaseAgent:
    def __init__(self, name: str, description: str, role: str, port: int):
        self.name = name
        self.description = description
        self.role = role
        self.port = port
        self._task_response_cache = {}
        self._stream_response_cache = {}
        self._workflow_work_lists = {}
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
        }

    async def execute_stream(self, payload):
        # 默认的流式状态汇报
        yield f"data: {json.dumps({'status': 'Working', 'message': f'{self.name} processing stream'})}\n\n"
        await asyncio.sleep(1)
        yield f"data: {json.dumps({'status': 'Completed', 'message': 'Done'})}\n\n"

    def _work_item_from_payload(self, payload):
        return payload.get("work_item") or payload.get("task_id", "work-item-001")

    def handle_message(self, payload, token):
        return {
            "work_item": self._work_item_from_payload(payload),
            "workflow_id": payload.get("workflow_id"),
            "status": "Accepted",
            "message": f"{self.name} received work item {payload.get('command')}",
            "work_list_size": len(self.get_work_list(payload.get("workflow_id"))),
        }

    def _capture_work_list(self, payload):
        workflow_id = payload.get("workflow_id")
        work_list = payload.get("work_list")
        if workflow_id and isinstance(work_list, list):
            with self._state_lock:
                self._workflow_work_lists[workflow_id] = deepcopy(work_list)

    def get_work_list(self, workflow_id):
        with self._state_lock:
            return deepcopy(self._workflow_work_lists.get(workflow_id, []))

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
            self._capture_work_list(payload)
            work_item = self._work_item_from_payload(payload)
            with self._state_lock:
                cached_response = self._task_response_cache.get(work_item)
            if cached_response is not None:
                return cached_response

            response = self.handle_message(payload, token)
            with self._state_lock:
                self._task_response_cache[work_item] = response
            return JSONResponse(response)
        
        @self.app.post("/sendMessageStream")
        async def send_message_stream(payload: dict, token: str = Depends(verify_token)):
            # 引入流式SSE返回
            return StreamingResponse(self._cached_stream(payload), media_type="text/event-stream")

    def start(self):
        uvicorn.run(self.app, host="0.0.0.0", port=self.port)

from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.responses import StreamingResponse
import uvicorn
import asyncio
import json
import os
from urllib.parse import urljoin

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
        self._task_response_cache = {}
        self._stream_response_cache = {}
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
            "sendMessageStreamEndpoint": "/sendMessageStream"
        }

    async def execute_stream(self, payload):
        # 默认的流式状态汇报
        yield f"data: {json.dumps({'status': 'Working', 'message': f'{self.name} processing stream'})}\n\n"
        await asyncio.sleep(1)
        yield f"data: {json.dumps({'status': 'Completed', 'message': 'Done'})}\n\n"

    def _task_id_from_payload(self, payload):
        return payload.get("task_id", "t-001")

    async def _replay_stream(self, cached_events):
        for event in cached_events:
            yield event

    async def _cached_stream(self, payload):
        task_id = self._task_id_from_payload(payload)
        cached_events = self._stream_response_cache.get(task_id)
        if cached_events is not None:
            async for event in self._replay_stream(cached_events):
                yield event
            return

        buffered_events = []
        async for event in self.execute_stream(payload):
            buffered_events.append(event)
            yield event
        self._stream_response_cache[task_id] = buffered_events

    def setup_routes(self):
        @self.app.get("/.well-known/agent-card")
        async def agent_card():
            return self.get_agent_card()

        @self.app.post("/sendMessage")
        async def send_message(payload: dict, token: str = Depends(verify_token)):
            task_id = self._task_id_from_payload(payload)
            if task_id in self._task_response_cache:
                return self._task_response_cache[task_id]

            response = {
                "task_id": task_id,
                "status": "Accepted",
                "message": f"{self.name} received task {payload.get('command')}"
            }
            self._task_response_cache[task_id] = response
            return response
        
        @self.app.post("/sendMessageStream")
        async def send_message_stream(payload: dict, token: str = Depends(verify_token)):
            # 引入流式SSE返回
            return StreamingResponse(self._cached_stream(payload), media_type="text/event-stream")

    def start(self):
        uvicorn.run(self.app, host="0.0.0.0", port=self.port)


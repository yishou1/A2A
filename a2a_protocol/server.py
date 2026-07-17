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
from observability import exception_diagnostics, log_event
from resource_monitor import ResourceMonitor, utc_now_iso
from model_registry import ModelRegistry
from idempotency_store import IdempotencyStore
from protocol_contracts import (
    ContractValidationError,
    PROTOCOL_VERSION,
    validate_task_payload,
    validate_task_response,
)
from skill_catalog import enrich_skill_contract, professional_skills_for_role

def verify_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return authorization.split("Bearer ")[1]

DEFAULT_ROLE_SKILLS = {
    "recon": [
        {
            "id": "scan_beach_defenses",
            "name": "Beach Defense Reconnaissance",
            "description": "探测/侦察滩头防御、敌方阵地和环境信息。",
            "tags": ["recon", "reconnaissance", "detect", "探测", "侦察"],
        }
    ],
    "artillery": [
        {
            "id": "suppress_beach_sector_A",
            "name": "Artillery Suppression",
            "description": "对指定区域执行火力压制并返回打击结果。",
            "tags": ["artillery", "suppression", "firepower", "火力压制"],
        }
    ],
    "evaluator": [
        {
            "id": "evaluate_strike",
            "name": "Strike Evaluation",
            "description": "评估侦察和火力压制结果，给出效果评分。",
            "tags": ["evaluate", "assessment", "score", "评估"],
        }
    ],
    "assault": [
        {
            "id": "capture_beachhead",
            "name": "Beachhead Assault",
            "description": "执行突击占领任务并返回突击结果。",
            "tags": ["assault", "capture", "突击", "占领"],
        }
    ],
    "execution_control": [
        {
            "id": "generate_execution_commands",
            "name": "Execution Control Planning",
            "description": "基于态势与规则生成可执行 strike/assault 指令。",
            "tags": ["execution_control", "planning", "command", "执行控制"],
        },
        {
            "id": "plan_strike_control",
            "name": "Strike Execution Control",
            "description": "火力压制阶段：关联规则 + 运动预测，生成 strike 指令。",
            "tags": ["execution_control", "strike", "planning", "火力控制"],
        },
        {
            "id": "plan_assault_control",
            "name": "Assault Execution Control",
            "description": "突击阶段：关联规则 + 运动预测，生成 assault 指令。",
            "tags": ["execution_control", "assault", "planning", "突击控制"],
        },
    ],
    "closed_loop": [
        {
            "id": "closed_loop_optimization",
            "name": "Closed Loop Optimization",
            "description": "执行闭环评估与优化并返回结构化结果。",
            "tags": ["closed_loop", "optimization", "闭环", "优化"],
        }
    ],
}


def default_skills_for_role(role: str) -> list[dict]:
    """Return the built-in demo skills plus the professional capability skills."""
    skills = [enrich_skill_contract(skill) for skill in DEFAULT_ROLE_SKILLS.get(role, [])]
    known_ids = {skill.get("id") for skill in skills}
    for professional in professional_skills_for_role(role):
        if professional.get("id") not in known_ids:
            skills.append(enrich_skill_contract(professional))
    return skills


def skill_tokens(skills: list[dict]) -> list[str]:
    tokens = []
    for skill in skills or []:
        for value in [
            skill.get("id"),
            skill.get("name"),
            skill.get("description"),
            *(skill.get("tags") or []),
        ]:
            if value:
                tokens.append(str(value))
    seen = set()
    unique = []
    for token in tokens:
        key = token.lower()
        if key not in seen:
            seen.add(key)
            unique.append(token)
    return unique


def skills_metadata(skills: list[dict]) -> dict:
    skill_ids = [str(skill.get("id")) for skill in skills or [] if skill.get("id")]
    return {
        "skill_ids": ",".join(skill_ids),
        "skills": ",".join(skill_tokens(skills)),
    }


class A2ABaseAgent:
    def __init__(
        self,
        name: str,
        description: str,
        role: str,
        port: int,
        skills: list[dict] = None,
        resource_monitor=None,
        models=None,
        idempotency_db_path: str | None = None,
        max_concurrent_tasks: int | None = None,
    ):
        self.name = name
        self.description = description
        self.role = role
        self.port = port
        self.skills = (
            [enrich_skill_contract(skill) for skill in skills]
            if skills is not None
            else default_skills_for_role(role)
        )
        self.started_at = time.time()
        self.ready = True
        self.resource_monitor = resource_monitor or ResourceMonitor()
        configured_concurrency = (
            max_concurrent_tasks
            if max_concurrent_tasks is not None
            else os.environ.get("A2A_AGENT_MAX_CONCURRENT_TASKS", "1")
        )
        self.max_concurrent_tasks = max(1, int(configured_concurrency))
        self.model_registry = models if isinstance(models, ModelRegistry) else ModelRegistry(models)
        state_db = idempotency_db_path or os.environ.get(
            "A2A_AGENT_STATE_DB",
            os.path.join(".a2a_state", "agent_idempotency.db"),
        )
        self.idempotency_store = IdempotencyStore(
            state_db,
            namespace=f"{self.name}:{self.port}",
        )
        self._task_response_cache = {}
        self._stream_response_cache = {}
        self._workflow_work_lists = {}
        self._recovery_notices = []
        self._metrics = {
            "tasks_received": 0,
            "tasks_completed": 0,
            "tasks_failed": 0,
            "stream_requests": 0,
            "cache_hits": 0,
            "active_tasks": 0,
            "total_duration_ms": 0.0,
            "last_error": None,
            "last_work_item": None,
        }
        self._last_error_details = None
        self._state_lock = threading.RLock()
        self.app = FastAPI(title=name)
        self.setup_routes()

    def get_agent_card(self):
        auth_server_base = os.environ.get("A2A_AUTH_SERVER_BASE", "http://127.0.0.1:8080")
        auth_server_base = auth_server_base.rstrip("/") + "/"
        card = {
            "protocolVersion": PROTOCOL_VERSION,
            "name": self.name,
            "description": self.description,
            "role": self.role,
            "skills": deepcopy(self.skills),
            "models": self.model_registry.list_models(),
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
            "resourcesEndpoint": "/resources",
            "modelsEndpoint": "/models",
            "recoveryEndpoint": "/recovery/notify",
            "maxConcurrentTasks": self.max_concurrent_tasks,
        }
        if self.skills:
            card["skills"] = self.skills
        return card

    async def handle_message(self, payload: dict):
        return {
            "task_id": self._task_id_from_payload(payload),
            "status": "Accepted",
            "message": f"{self.name} received task {payload.get('command')}"
        }

    def skill_definition(self, skill_id: str | None) -> dict | None:
        if not skill_id:
            return None
        return next(
            (skill for skill in self.skills if skill.get("id") == skill_id),
            None,
        )

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
        output, message = self.execute_task(payload)
        yield f"data: {json.dumps({'status': 'Completed', 'message': message, 'output': output})}\n\n"

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
        resource_snapshot = self.resource_snapshot()
        snapshot.update(
            {
                "agent": self.name,
                "role": self.role,
                "port": self.port,
                "ready": self.ready,
                "uptime_seconds": round(time.time() - self.started_at, 3),
                "resources": resource_snapshot,
            }
        )
        return snapshot

    def resource_snapshot(self):
        return self.resource_monitor.snapshot()

    def heartbeat_metadata(self):
        metadata = dict(self.resource_monitor.heartbeat_metadata())
        metadata.update(self.model_registry.metadata())
        with self._state_lock:
            active_tasks = self._metrics["active_tasks"]
            completed = self._metrics["tasks_completed"]
            failed = self._metrics["tasks_failed"]
            total = completed + failed
            success_rate = completed / total if total else 1.0
            average_latency = self._metrics["total_duration_ms"] / total if total else 0.0
        metadata["agent_run_state"] = "ready" if self.ready else "not_ready"
        metadata["status"] = "busy" if active_tasks > 0 else "idle"
        metadata["task_execution_status"] = self._task_execution_status(active_tasks)
        metadata["active_tasks"] = str(active_tasks)
        metadata["max_concurrent_tasks"] = str(self.max_concurrent_tasks)
        metadata["available_task_slots"] = str(max(0, self.max_concurrent_tasks - active_tasks))
        metadata["quality_tasks_completed"] = str(completed)
        metadata["quality_tasks_failed"] = str(failed)
        metadata["quality_success_rate"] = round(success_rate, 6)
        metadata["quality_avg_latency_ms"] = round(average_latency, 3)
        return metadata

    def _task_execution_status(self, active_tasks: int) -> str:
        if active_tasks <= 0:
            return "idle"
        if active_tasks >= self.max_concurrent_tasks:
            return "saturated"
        return "busy"

    def notify_recovery(self, notice: dict) -> dict:
        """Handle a scheduler recovery notification after topology/plan changes.

        The scheduler calls this after rebuilding the topology or re-planning
        tasks so the Agent knows to continue executing.
        """
        notice = notice or {}
        record = {
            "received_at": utc_now_iso(),
            "workflow_id": notice.get("workflow_id"),
            "action": notice.get("action", "resume"),
            "reason": notice.get("reason"),
            "detail": deepcopy(notice.get("detail")) if notice.get("detail") else None,
        }
        with self._state_lock:
            self._recovery_notices.append(record)
            if len(self._recovery_notices) > 100:
                self._recovery_notices = self._recovery_notices[-100:]
            if notice.get("reset_cache"):
                workflow_id = notice.get("workflow_id")
                if workflow_id:
                    self._task_response_cache = {
                        key: value
                        for key, value in self._task_response_cache.items()
                        if value.get("workflow_id") != workflow_id
                    }
                    self._stream_response_cache.clear()
                else:
                    self._task_response_cache.clear()
                    self._stream_response_cache.clear()
        # Recovery notifications always return the Agent to a ready state so it
        # can resume executing the re-planned workflow.
        self.ready = True
        log_event(
            "agent_recovery_notified",
            agent=self.name,
            role=self.role,
            workflow_id=record["workflow_id"],
            action=record["action"],
        )
        return {
            "acknowledged": True,
            "agent": self.name,
            "role": self.role,
            "ready": self.ready,
            "recovery": record,
        }

    def recovery_notices(self) -> list:
        with self._state_lock:
            return deepcopy(self._recovery_notices)

    def can_accept_task(self):
        with self._state_lock:
            return self._can_accept_task_locked()

    def _can_accept_task_locked(self):
        if not self.ready:
            return False, "agent is not ready", "AGENT_NOT_READY"
        if self._metrics["active_tasks"] >= self.max_concurrent_tasks:
            return False, "agent task capacity is full", "AGENT_RESOURCE_EXHAUSTED"
        return True, None, None

    def _reserve_task_capacity(self, work_item: str):
        with self._state_lock:
            accepted, error, error_code = self._can_accept_task_locked()
            if not accepted:
                return False, error, error_code
            self._metrics["tasks_received"] += 1
            self._metrics["active_tasks"] += 1
            self._metrics["last_work_item"] = work_item
            return True, None, None

    def _release_task_capacity(self):
        with self._state_lock:
            self._metrics["active_tasks"] = max(0, self._metrics["active_tasks"] - 1)

    def last_error_diagnostics(self):
        with self._state_lock:
            return deepcopy(self._last_error_details)

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
            resources = self.resource_snapshot()
            return {
                "status": "ok",
                "agent": self.name,
                "role": self.role,
                "uptime_seconds": round(time.time() - self.started_at, 3),
                "resource_monitor_available": resources.get("monitor_available"),
            }

        @self.app.get("/ready")
        async def ready():
            with self._state_lock:
                active_tasks = self._metrics["active_tasks"]
            resources = self.resource_snapshot()
            return {
                "ready": self.ready,
                "agent": self.name,
                "role": self.role,
                "active_tasks": active_tasks,
                "max_concurrent_tasks": self.max_concurrent_tasks,
                "available_task_slots": max(0, self.max_concurrent_tasks - active_tasks),
                "task_execution_status": self._task_execution_status(active_tasks),
                "manual_ready": self.ready,
                "resource_monitor_available": resources.get("monitor_available"),
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

        @self.app.get("/resources")
        async def resources():
            return {
                "agent": self.name,
                "role": self.role,
                "port": self.port,
                **self.resource_snapshot(),
            }

        @self.app.get("/models")
        async def models():
            return {
                "agent": self.name,
                "role": self.role,
                **self.model_registry.snapshot(),
            }

        @self.app.post("/recovery/notify")
        async def recovery_notify(payload: dict):
            return self.notify_recovery(payload)

        @self.app.get("/recovery/status")
        async def recovery_status():
            notices = self.recovery_notices()
            return {
                "agent": self.name,
                "role": self.role,
                "ready": self.ready,
                "recovery_notices": notices,
                "last_recovery": notices[-1] if notices else None,
            }

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
            skill_id = (
                payload.get("required_skill")
                or next(iter(payload.get("required_skills") or []), None)
                or payload.get("command")
            )
            try:
                payload = validate_task_payload(payload, self.skill_definition(skill_id))
            except ContractValidationError as exc:
                return build_task_error_response(
                    workflow_id=payload.get("workflow_id"),
                    work_item=self._work_item_from_payload(payload),
                    agent=self.name,
                    role=self.role,
                    command=payload.get("command"),
                    error=str(exc),
                    error_code=exc.code,
                )
            self._capture_work_list(payload)
            work_item = self._work_item_from_payload(payload)
            with self._state_lock:
                cached_response = self._task_response_cache.get(work_item)
            if cached_response is None:
                cached_response = self.idempotency_store.get(work_item)
            if cached_response is not None:
                try:
                    validate_task_response(
                        payload,
                        cached_response,
                        self.skill_definition(skill_id),
                    )
                except ContractValidationError:
                    with self._state_lock:
                        self._task_response_cache.pop(work_item, None)
                    self.idempotency_store.delete(work_item)
                    cached_response = None
            if cached_response is not None:
                with self._state_lock:
                    self._metrics["cache_hits"] += 1
                cached = deepcopy(cached_response)
                cached["cached"] = True
                return cached

            started = time.perf_counter()
            accepted, error, error_code = self._reserve_task_capacity(work_item)
            if not accepted:
                return build_task_error_response(
                    workflow_id=payload.get("workflow_id"),
                    work_item=work_item,
                    agent=self.name,
                    role=self.role,
                    command=payload.get("command"),
                    error=error,
                    error_code=error_code,
                )
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
                validate_task_response(payload, response, self.skill_definition(skill_id))
                with self._state_lock:
                    self._metrics["tasks_completed"] += 1
                    self._metrics["total_duration_ms"] += duration_ms
                    self._task_response_cache[work_item] = response
                self.idempotency_store.put(work_item, response)
                return response
            except Exception as exc:
                diagnostics = exception_diagnostics(exc)
                duration_ms = round((time.perf_counter() - started) * 1000, 3)
                response = build_task_error_response(
                    workflow_id=payload.get("workflow_id"),
                    work_item=work_item,
                    agent=self.name,
                    role=self.role,
                    command=payload.get("command"),
                    error=str(exc),
                    error_code=getattr(exc, "code", "AGENT_BUSINESS_ERROR"),
                    metrics={
                        "latency_ms": duration_ms,
                        "duration_ms": duration_ms,
                    },
                )
                with self._state_lock:
                    self._metrics["tasks_failed"] += 1
                    self._metrics["total_duration_ms"] += duration_ms
                    self._metrics["last_error"] = str(exc)
                    self._last_error_details = diagnostics
                log_event(
                    "agent_task_failed",
                    agent=self.name,
                    role=self.role,
                    workflow_id=payload.get("workflow_id"),
                    work_item=work_item,
                    **diagnostics,
                )
                return response
            finally:
                self._release_task_capacity()
        
        @self.app.post("/sendMessageStream")
        async def send_message_stream(payload: dict, token: str = Depends(verify_token)):
            skill_id = (
                payload.get("required_skill")
                or next(iter(payload.get("required_skills") or []), None)
                or payload.get("command")
            )
            try:
                payload = validate_task_payload(payload, self.skill_definition(skill_id))
            except ContractValidationError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            work_item = self._work_item_from_payload(payload)
            accepted, error, error_code = self._reserve_task_capacity(work_item)
            if not accepted:
                raise HTTPException(status_code=503, detail={"error": error, "error_code": error_code})
            with self._state_lock:
                self._metrics["stream_requests"] += 1

            async def capacity_guarded_stream():
                try:
                    async for event in self._cached_stream(payload):
                        yield event
                finally:
                    self._release_task_capacity()

            return StreamingResponse(capacity_guarded_stream(), media_type="text/event-stream")

    def start(self):
        uvicorn.run(self.app, host="0.0.0.0", port=self.port)

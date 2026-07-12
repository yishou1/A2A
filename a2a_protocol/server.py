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
from resource_monitor import ResourceMonitor
from supervisor import supervisor_from_env
from task_pool import JsonTaskPool

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
}


def default_skills_for_role(role: str) -> list[dict]:
    return deepcopy(DEFAULT_ROLE_SKILLS.get(role, []))


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
    return {"skills": ",".join(skill_tokens(skills))}


class A2ABaseAgent:
    def __init__(
        self,
        name: str,
        description: str,
        role: str,
        port: int,
        skills: list[dict] = None,
        resource_monitor=None,
        supervisor=None,
        agent_id: str = None,
        endpoint: str = None,
        max_concurrency: int = None,
    ):
        self.name = name
        self.description = description
        self.role = role
        self.port = port
        self.agent_id = agent_id or os.environ.get("A2A_AGENT_ID") or name
        default_host = os.environ.get("A2A_AGENT_HOST", "127.0.0.1")
        self.endpoint = endpoint or os.environ.get("A2A_AGENT_PUBLIC_URL") or f"http://{default_host}:{port}"
        self.skills = deepcopy(skills) if skills is not None else default_skills_for_role(role)
        self.started_at = time.time()
        self.ready = True
        self.resource_monitor = resource_monitor or ResourceMonitor()
        self.supervisor = supervisor if supervisor is not None else supervisor_from_env()
        self.supervisor_enabled = (
            os.environ.get("A2A_SUPERVISOR_ENABLED", "true").lower()
            not in {"0", "false", "no", "off"}
        )
        self.supervisor_heartbeat_interval = float(os.environ.get("A2A_SUPERVISOR_HEARTBEAT_INTERVAL", "5"))
        self.max_concurrency = max(
            1,
            int(max_concurrency if max_concurrency is not None else os.environ.get("A2A_AGENT_MAX_CONCURRENCY", "1")),
        )
        self._supervisor_stop_event = threading.Event()
        self._supervisor_thread = None
        self.reject_when_resource_critical = (
            os.environ.get("A2A_REJECT_WHEN_RESOURCE_CRITICAL", "true").lower()
            not in {"0", "false", "no", "off"}
        )
        self._task_response_cache = {}
        self._stream_response_cache = {}
        self._workflow_work_lists = {}
        self._agent_work_list = []
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
        self._last_error_details = None
        self._state_lock = threading.RLock()
        self.app = FastAPI(title=name)
        self.setup_routes()

    def get_agent_card(self):
        auth_server_base = os.environ.get("A2A_AUTH_SERVER_BASE", "http://127.0.0.1:8080")
        auth_server_base = auth_server_base.rstrip("/") + "/"
        return {
            "name": self.name,
            "agent_id": self.agent_id,
            "description": self.description,
            "role": self.role,
            "endpoint": self.endpoint,
            "skills": deepcopy(self.skills),
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

    def get_agent_work_list(self):
        with self._state_lock:
            return deepcopy(self._agent_work_list)

    def _upsert_agent_work_item(self, payload: dict, status: str, *, response: dict = None, error: str = None):
        work_item = self._work_item_from_payload(payload)
        activity = payload.get("activity") or {}
        entry = {
            "work_item": work_item,
            "workflow_id": payload.get("workflow_id"),
            "activity_id": payload.get("activity_id") or payload.get("activatity_id") or activity.get("id"),
            "activity_index": payload.get("activity_index") or payload.get("activatity_index") or activity.get("index"),
            "activity_skill": payload.get("activity_skill") or payload.get("required_skill") or activity.get("skill"),
            "required_skill": payload.get("required_skill") or activity.get("required_skill"),
            "required_skills": list(payload.get("required_skills") or activity.get("required_skills") or []),
            "status": status,
            "error": error,
            "updated_at": time.time(),
        }
        if response is not None:
            entry["response"] = deepcopy(response)
        with self._state_lock:
            for index, existing in enumerate(self._agent_work_list):
                if existing.get("work_item") == work_item:
                    self._agent_work_list[index] = {**existing, **entry}
                    return deepcopy(self._agent_work_list[index])
            self._agent_work_list.append(entry)
            return deepcopy(entry)

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
                "resource_ready": self.resource_ready(),
                "uptime_seconds": round(time.time() - self.started_at, 3),
                "resources": resource_snapshot,
            }
        )
        return snapshot

    def supervisor_payload(self) -> dict:
        with self._state_lock:
            active_tasks = int(self._metrics.get("active_tasks", 0) or 0)
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role,
            "endpoint": self.endpoint,
            "skills": skill_tokens(self.skills),
            "ready": self.ready and self.resource_ready(),
            "status": "online",
            "resources": self.resource_snapshot(),
            "active_tasks": active_tasks,
            "max_concurrency": self.max_concurrency,
            "metadata": {
                "port": self.port,
                "uptime_seconds": round(time.time() - self.started_at, 3),
            },
        }

    def register_with_supervisor(self):
        if not self.supervisor_enabled or self.supervisor is None:
            return None
        try:
            return self.supervisor.register_agent(self.supervisor_payload())
        except Exception as exc:
            print(f"[SUPERVISOR] register failed for {self.agent_id}: {exc}")
            return None

    def heartbeat_to_supervisor(self):
        if not self.supervisor_enabled or self.supervisor is None:
            return None
        try:
            return self.supervisor.heartbeat(self.agent_id, self.supervisor_payload())
        except Exception as exc:
            print(f"[SUPERVISOR] heartbeat failed for {self.agent_id}: {exc}")
            return None

    def _supervisor_heartbeat_loop(self):
        self.register_with_supervisor()
        while not self._supervisor_stop_event.wait(self.supervisor_heartbeat_interval):
            self.heartbeat_to_supervisor()

    def start_supervisor_heartbeat(self):
        if not self.supervisor_enabled or self._supervisor_thread is not None:
            return
        self._supervisor_thread = threading.Thread(
            target=self._supervisor_heartbeat_loop,
            name=f"a2a-supervisor-heartbeat-{self.agent_id}",
            daemon=True,
        )
        self._supervisor_thread.start()

    def resource_snapshot(self):
        return self.resource_monitor.snapshot()

    def resource_ready(self):
        return self.resource_monitor.ready()

    def heartbeat_metadata(self):
        return self.resource_monitor.heartbeat_metadata()

    def can_accept_task(self):
        if not self.ready:
            return False, "agent is not ready", "AGENT_NOT_READY"
        if self.reject_when_resource_critical and not self.resource_ready():
            return False, "agent resource state is critical", "AGENT_RESOURCE_EXHAUSTED"
        return True, None, None

    @staticmethod
    def _classify_exception(exc: Exception) -> str:
        """Distinguish system-level failures from business errors for circuit breaker."""
        exc_type = type(exc).__name__
        exc_module = type(exc).__module__ or ""
        msg = str(exc).lower()

        # Connection / network errors → system failure
        if exc_type in {
            "Timeout", "ConnectionError", "ConnectionRefusedError",
            "ConnectionResetError", "ConnectionAbortedError", "BrokenPipeError",
        }:
            return "AGENT_UNAVAILABLE"

        # requests library errors
        if "requests" in exc_module:
            if "timeout" in exc_type.lower() or "timeout" in msg:
                return "AGENT_TIMEOUT"
            if "connection" in exc_type.lower() or "connection" in msg:
                return "AGENT_UNAVAILABLE"
            if "http" in exc_type.lower() or "httperror" in exc_type.lower():
                return "AGENT_HTTP_5XX"

        # HTTP / network markers in message
        if any(m in msg for m in ("connection refused", "connection reset", "connection aborted")):
            return "AGENT_UNAVAILABLE"
        if any(m in msg for m in ("timed out", "timeout", "read timed out")):
            return "AGENT_TIMEOUT"
        if any(m in msg for m in ("503", "502", "504", "500", "service unavailable")):
            return "AGENT_HTTP_5XX"
        if "heartbeat lost" in msg:
            return "AGENT_HEARTBEAT_LOST"
        if "not ready" in msg:
            return "AGENT_NOT_READY"

        # File / I/O errors from external dependencies → system failure
        if exc_type in {"FileNotFoundError", "PermissionError", "OSError", "IOError"}:
            return "AGENT_UNAVAILABLE"

        # Default: business error (algorithm returned unexpected result, invalid input, etc.)
        return "AGENT_BUSINESS_ERROR"

    def claim_and_execute_crowd_task(self, workflow_id: str = None):
        can_accept, reason, error_code = self.can_accept_task()
        if not can_accept:
            return {
                "claimed": False,
                "reason": reason,
                "error_code": error_code,
                "agent": self.name,
                "role": self.role,
            }
        self.heartbeat_to_supervisor()
        task_pool = JsonTaskPool.from_env()
        claim = task_pool.claim_next(
            agent_id=self.agent_id,
            agent_skills=skill_tokens(self.skills),
            workflow_id=workflow_id,
        )
        if not claim.get("claimed"):
            return claim

        payload = claim["payload"]
        self._capture_work_list(payload)
        self._upsert_agent_work_item(payload, "running")
        work_item = self._work_item_from_payload(payload)
        task_id = claim["task_id"]
        claim_id = claim.get("claim_id")
        lease_seconds = float(claim.get("lease_until_ts", 0) - time.time())
        if lease_seconds <= 0:
            lease_seconds = 60.0
        started = time.perf_counter()
        with self._state_lock:
            self._metrics["tasks_received"] += 1
            self._metrics["active_tasks"] += 1
            self._metrics["last_work_item"] = work_item
        self.heartbeat_to_supervisor()

        # ── background claim renewal for long-running tasks ──
        _renew_stop = threading.Event()
        _renew_interval = max(5.0, lease_seconds / 3.0)

        def _renew_loop():
            while not _renew_stop.wait(_renew_interval):
                try:
                    task_pool.renew_claim(
                        task_id,
                        claim_id=claim_id,
                        agent_id=self.agent_id,
                        lease_seconds=lease_seconds,
                    )
                except Exception:
                    pass

        _renew_thread = threading.Thread(
            target=_renew_loop,
            name=f"a2a-crowd-renew-{task_id}",
            daemon=True,
        )
        _renew_thread.start()

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
                extra={
                    "crowd_task_id": claim.get("task_id"),
                    "crowd_claim_id": claim.get("claim_id"),
                    "activity_id": payload.get("activity_id"),
                    "activity_skill": payload.get("activity_skill"),
                },
            )
            task_pool.submit_result(
                claim["task_id"],
                claim_id=claim.get("claim_id"),
                agent_id=self.agent_id,
                response=response,
            )
            self._upsert_agent_work_item(payload, "completed", response=response)
            with self._state_lock:
                self._metrics["tasks_completed"] += 1
                self._task_response_cache[work_item] = response
            return {
                "claimed": True,
                "task_id": claim.get("task_id"),
                "claim_id": claim.get("claim_id"),
                "response": response,
            }
        except Exception as exc:
            diagnostics = exception_diagnostics(exc)
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            error_code = self._classify_exception(exc)
            response = build_task_error_response(
                workflow_id=payload.get("workflow_id"),
                work_item=work_item,
                agent=self.name,
                role=self.role,
                command=payload.get("command"),
                error=str(exc),
                error_code=error_code,
                metrics={
                    "latency_ms": duration_ms,
                    "duration_ms": duration_ms,
                },
            )
            task_pool.submit_result(
                claim["task_id"],
                claim_id=claim.get("claim_id"),
                agent_id=self.agent_id,
                response=response,
            )
            self._upsert_agent_work_item(payload, "failed", response=response, error=str(exc))
            with self._state_lock:
                self._metrics["tasks_failed"] += 1
                self._metrics["last_error"] = str(exc)
                self._last_error_details = diagnostics
                self._task_response_cache[work_item] = response
            log_event(
                "agent_crowd_task_failed",
                agent=self.name,
                role=self.role,
                workflow_id=payload.get("workflow_id"),
                work_item=work_item,
                error_code=error_code,
                **diagnostics,
            )
            return {
                "claimed": True,
                "task_id": claim.get("task_id"),
                "claim_id": claim.get("claim_id"),
                "response": response,
            }
        finally:
            _renew_stop.set()
            with self._state_lock:
                self._metrics["active_tasks"] = max(0, self._metrics["active_tasks"] - 1)
            self.heartbeat_to_supervisor()

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
                "status": "ok" if resources.get("resource_state") != "critical" else "degraded",
                "agent": self.name,
                "role": self.role,
                "uptime_seconds": round(time.time() - self.started_at, 3),
                "resource_state": resources.get("resource_state"),
                "resource_monitor_available": resources.get("monitor_available"),
            }

        @self.app.get("/ready")
        async def ready():
            with self._state_lock:
                active_tasks = self._metrics["active_tasks"]
            resources = self.resource_snapshot()
            return {
                "ready": self.ready and resources.get("resource_state") != "critical",
                "agent": self.name,
                "role": self.role,
                "active_tasks": active_tasks,
                "manual_ready": self.ready,
                "resource_ready": resources.get("resource_state") != "critical",
                "resource_state": resources.get("resource_state"),
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

        @self.app.get("/work-list")
        async def agent_work_list():
            return {
                "agent": self.name,
                "role": self.role,
                "work_list": self.get_agent_work_list(),
            }

        @self.app.post("/crowd/claim-next")
        async def crowd_claim_next(payload: dict | None = None, token: str = Depends(verify_token)):
            if not self.ready:
                return {
                    "claimed": False,
                    "reason": "agent_not_ready",
                    "agent": self.name,
                    "role": self.role,
                }
            payload = payload or {}
            return self.claim_and_execute_crowd_task(workflow_id=payload.get("workflow_id"))

        @self.app.post("/sendMessage")
        async def send_message(payload: dict, token: str = Depends(verify_token)):
            accepted, error, error_code = self.can_accept_task()
            if not accepted:
                work_item = self._work_item_from_payload(payload)
                return build_task_error_response(
                    workflow_id=payload.get("workflow_id"),
                    work_item=work_item,
                    agent=self.name,
                    role=self.role,
                    command=payload.get("command"),
                    error=error,
                    error_code=error_code,
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
            self.heartbeat_to_supervisor()
            try:
                self._upsert_agent_work_item(payload, "running")
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
                self._upsert_agent_work_item(payload, "completed", response=response)
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
                    error_code=self._classify_exception(exc),
                    metrics={
                        "latency_ms": duration_ms,
                        "duration_ms": duration_ms,
                    },
                )
                with self._state_lock:
                    self._metrics["tasks_failed"] += 1
                    self._metrics["last_error"] = str(exc)
                    self._last_error_details = diagnostics
                    self._task_response_cache[work_item] = response
                self._upsert_agent_work_item(payload, "failed", response=response, error=str(exc))
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
                with self._state_lock:
                    self._metrics["active_tasks"] = max(0, self._metrics["active_tasks"] - 1)
                self.heartbeat_to_supervisor()
        
        @self.app.post("/sendMessageStream")
        async def send_message_stream(payload: dict, token: str = Depends(verify_token)):
            accepted, error, _ = self.can_accept_task()
            if not accepted:
                raise HTTPException(status_code=503, detail=error)
            with self._state_lock:
                self._metrics["stream_requests"] += 1
            return StreamingResponse(self._cached_stream(payload), media_type="text/event-stream")

    def start(self):
        self.start_supervisor_heartbeat()
        uvicorn.run(self.app, host="0.0.0.0", port=self.port)

from __future__ import annotations

import json
import os
import re
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ts() -> float:
    return time.time()


def _normalize_skill(value: Any) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").strip().lower())


def _split_skill_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = []
        for item in value:
            items.extend(_split_skill_values(item))
        return items
    if isinstance(value, dict):
        return _split_skill_values(
            [
                value.get("id"),
                value.get("name"),
                value.get("description"),
                value.get("tags"),
            ]
        )
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if parsed is not None and parsed is not value:
        return _split_skill_values(parsed)
    return [item.strip() for item in re.split(r"[,;]+", text) if item.strip()]


def _skill_set(values: Any) -> set[str]:
    return {_normalize_skill(item) for item in _split_skill_values(values) if _normalize_skill(item)}


def _float_value(value: Any):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class _CrossProcessFileLock:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = None

    def __enter__(self):
        self._handle = self.path.open("a+b")
        if os.name == "nt":
            import msvcrt

            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb):
        if not self._handle:
            return
        if os.name == "nt":
            import msvcrt

            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None


class SupervisorStore:
    """
    File-backed Agent supervisor registry.

    It owns Agent registration, heartbeat/resource snapshots, readiness,
    circuit-breaker state, and claim admission decisions. TaskPool asks this
    store whether an Agent may claim a task; Commander stays out of Agent
    selection in crowd mode.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        heartbeat_timeout_seconds: float | None = None,
        circuit_failure_threshold: int | None = None,
        circuit_recovery_timeout: float | None = None,
    ):
        default_path = Path(__file__).resolve().parent / ".a2a_state" / "supervisor.json"
        self.path = Path(path or os.environ.get("A2A_SUPERVISOR_PATH") or default_path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.heartbeat_timeout_seconds = float(
            heartbeat_timeout_seconds
            if heartbeat_timeout_seconds is not None
            else os.environ.get("A2A_SUPERVISOR_HEARTBEAT_TIMEOUT", "30")
        )
        self.circuit_failure_threshold = max(
            1,
            int(
                circuit_failure_threshold
                if circuit_failure_threshold is not None
                else os.environ.get("A2A_SUPERVISOR_CIRCUIT_FAILURE_THRESHOLD", "3")
            ),
        )
        self.circuit_recovery_timeout = max(
            1.0,
            float(
                circuit_recovery_timeout
                if circuit_recovery_timeout is not None
                else os.environ.get("A2A_SUPERVISOR_CIRCUIT_RECOVERY_TIMEOUT", "30")
            ),
        )
        self._thread_lock = threading.RLock()
        self._circuits: dict[str, dict] = {}
        self._circuits_lock = threading.RLock()
        # Restore persisted circuit breaker state on init
        self._restore_circuits()

    @classmethod
    def from_env(cls) -> "SupervisorStore":
        return cls(
            os.environ.get("A2A_SUPERVISOR_PATH"),
            heartbeat_timeout_seconds=float(os.environ.get("A2A_SUPERVISOR_HEARTBEAT_TIMEOUT", "30")),
        )

    @contextmanager
    def _locked_state(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._thread_lock:
            with _CrossProcessFileLock(self.lock_path):
                state = self._load_unlocked()
                yield state
                self._save_unlocked(state)

    def _load_unlocked(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "agents": {}, "circuits": {}}
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                state = json.load(handle)
        except json.JSONDecodeError:
            state = {"version": 1, "agents": {}, "circuits": {}}
        state.setdefault("version", 1)
        state.setdefault("agents", {})
        state.setdefault("circuits", {})
        return state

    def _save_unlocked(self, state: dict) -> None:
        # Persist current circuit breaker state
        with self._circuits_lock:
            state["circuits"] = deepcopy(self._circuits)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.path)

    def _mark_stale_agents(self, agents: dict, now_ts: float | None = None) -> None:
        now_ts = now_ts or _now_ts()
        for agent in agents.values():
            last_seen_ts = float(agent.get("last_heartbeat_ts") or agent.get("registered_at_ts") or 0)
            if last_seen_ts and now_ts - last_seen_ts > self.heartbeat_timeout_seconds:
                agent["status"] = "offline"
                agent["ready"] = False
                agent["offline_reason"] = "heartbeat_timeout"

    def register_agent(self, payload: dict) -> dict:
        agent_id = payload.get("agent_id") or payload.get("name")
        if not agent_id:
            raise ValueError("agent registration requires agent_id or name")
        now = utc_now_iso()
        now_ts = _now_ts()
        resources = deepcopy(payload.get("resources", {}) or {})
        skills = _split_skill_values(payload.get("skills"))
        with self._locked_state() as state:
            agents = state.setdefault("agents", {})
            existing = agents.get(agent_id, {})
            agent = {
                **existing,
                "agent_id": agent_id,
                "name": payload.get("name") or existing.get("name") or agent_id,
                "role": payload.get("role") or existing.get("role"),
                "endpoint": payload.get("endpoint") or existing.get("endpoint"),
                "skills": skills or existing.get("skills", []),
                "resources": resources or existing.get("resources", {}),
                "ready": bool(payload.get("ready", existing.get("ready", True))),
                "status": payload.get("status") or "online",
                "active_tasks": int(payload.get("active_tasks", existing.get("active_tasks", 0)) or 0),
                "max_concurrency": int(payload.get("max_concurrency", existing.get("max_concurrency", 1)) or 1),
                "metadata": deepcopy(payload.get("metadata", existing.get("metadata", {})) or {}),
                "registered_at": existing.get("registered_at") or now,
                "registered_at_ts": existing.get("registered_at_ts") or now_ts,
                "last_heartbeat_at": now,
                "last_heartbeat_ts": now_ts,
                "updated_at": now,
            }
            agents[agent_id] = agent
            return deepcopy(agent)

    def heartbeat(self, agent_id: str, payload: dict) -> dict:
        if not agent_id:
            raise ValueError("agent_id is required")
        now = utc_now_iso()
        now_ts = _now_ts()
        with self._locked_state() as state:
            agents = state.setdefault("agents", {})
            existing = agents.get(agent_id, {"agent_id": agent_id, "registered_at": now, "registered_at_ts": now_ts})
            if "skills" in payload:
                existing["skills"] = _split_skill_values(payload.get("skills"))
            if "resources" in payload:
                existing["resources"] = deepcopy(payload.get("resources") or {})
            for key in ("name", "role", "endpoint", "metadata"):
                if key in payload:
                    existing[key] = deepcopy(payload.get(key))
            if "ready" in payload:
                existing["ready"] = bool(payload.get("ready"))
            if "status" in payload:
                existing["status"] = payload.get("status") or "online"
            else:
                existing["status"] = "online"
            if "active_tasks" in payload:
                existing["active_tasks"] = max(0, int(payload.get("active_tasks") or 0))
            if "max_concurrency" in payload:
                existing["max_concurrency"] = max(1, int(payload.get("max_concurrency") or 1))
            existing["last_heartbeat_at"] = now
            existing["last_heartbeat_ts"] = now_ts
            existing["updated_at"] = now
            agents[agent_id] = existing
            return deepcopy(existing)

    def list_agents(self, *, include_offline: bool = True) -> list[dict]:
        with self._locked_state() as state:
            agents = state.setdefault("agents", {})
            self._mark_stale_agents(agents)
            values = list(agents.values())
            if not include_offline:
                values = [agent for agent in values if agent.get("status") != "offline"]
            return deepcopy(sorted(values, key=lambda agent: agent.get("agent_id", "")))

    def get_agent(self, agent_id: str) -> dict | None:
        with self._locked_state() as state:
            agents = state.setdefault("agents", {})
            self._mark_stale_agents(agents)
            agent = agents.get(agent_id)
            return deepcopy(agent) if agent else None

    def task_started(self, agent_id: str, *, task_id: str = None, work_item: str = None) -> dict | None:
        with self._locked_state() as state:
            agent = state.setdefault("agents", {}).get(agent_id)
            if not agent:
                return None
            agent["active_tasks"] = max(0, int(agent.get("active_tasks", 0) or 0)) + 1
            agent.setdefault("active_work_items", [])
            if work_item and work_item not in agent["active_work_items"]:
                agent["active_work_items"].append(work_item)
            agent["last_task_id"] = task_id
            agent["updated_at"] = utc_now_iso()
            return deepcopy(agent)

    def task_finished(self, agent_id: str, *, work_item: str = None) -> dict | None:
        with self._locked_state() as state:
            agent = state.setdefault("agents", {}).get(agent_id)
            if not agent:
                return None
            agent["active_tasks"] = max(0, int(agent.get("active_tasks", 0) or 0) - 1)
            active_work_items = list(agent.get("active_work_items", []))
            if work_item in active_work_items:
                active_work_items.remove(work_item)
            agent["active_work_items"] = active_work_items
            agent["updated_at"] = utc_now_iso()
            return deepcopy(agent)

    # ── circuit breaker ──────────────────────────────────────────────

    def _restore_circuits(self) -> None:
        """Lazily load persisted circuit state from file if not already in memory."""
        if self._circuits:
            return
        try:
            if self.path.exists():
                with self.path.open("r", encoding="utf-8") as handle:
                    raw = json.load(handle)
                persisted = raw.get("circuits", {})
                if persisted:
                    with self._circuits_lock:
                        if not self._circuits:
                            self._circuits = deepcopy(persisted)
        except (json.JSONDecodeError, OSError):
            pass

    def _circuit_key(self, agent_id: str) -> str:
        return str(agent_id)

    def _circuit_snapshot(self, agent_id: str) -> dict:
        key = self._circuit_key(agent_id)
        with self._circuits_lock:
            rec = self._circuits.get(key)
            if rec is None:
                return {
                    "state": "closed",
                    "failure_count": 0,
                    "opened_at_ts": None,
                    "open_until_ts": None,
                }
            return {
                "state": rec["state"],
                "failure_count": rec["failure_count"],
                "opened_at_ts": rec.get("opened_at_ts"),
                "open_until_ts": rec.get("open_until_ts"),
            }

    def is_agent_circuit_open(self, agent_id: str) -> bool:
        snap = self._circuit_snapshot(agent_id)
        if snap["state"] == "closed":
            return False
        if snap["state"] == "open":
            if _now_ts() >= (snap["open_until_ts"] or 0):
                return False
            return True
        return False

    def record_agent_failure(self, agent_id: str, *, error_message: str = None) -> dict:
        key = self._circuit_key(agent_id)
        with self._circuits_lock:
            rec = self._circuits.setdefault(
                key,
                {"state": "closed", "failure_count": 0, "opened_at_ts": None, "open_until_ts": None},
            )
            rec["failure_count"] = rec.get("failure_count", 0) + 1
            now = _now_ts()
            if rec["state"] == "half_open" or rec["failure_count"] >= self.circuit_failure_threshold:
                rec["state"] = "open"
                rec["opened_at_ts"] = now
                rec["open_until_ts"] = now + self.circuit_recovery_timeout
            return {
                "state": rec["state"],
                "failure_count": rec["failure_count"],
                "opened_at_ts": rec.get("opened_at_ts"),
                "open_until_ts": rec.get("open_until_ts"),
            }

    def record_agent_success(self, agent_id: str) -> dict:
        key = self._circuit_key(agent_id)
        with self._circuits_lock:
            prev = self._circuits.pop(key, None)
            self._circuits[key] = {
                "state": "closed",
                "failure_count": 0,
                "opened_at_ts": None,
                "open_until_ts": None,
            }
            return {
                "state": "closed",
                "failure_count": 0,
                "opened_at_ts": None,
                "open_until_ts": None,
                "previous_state": prev.get("state") if prev else "closed",
            }

    # ── online check ─────────────────────────────────────────────────

    def is_agent_online(self, agent_id: str) -> bool:
        agent = self.get_agent(agent_id)
        if not agent:
            return False
        return agent.get("status") == "online"

    # ── claim admission ──────────────────────────────────────────────

    def evaluate_claim(
        self,
        agent_id: str,
        *,
        required_skills: Any = None,
        resource_requirements: dict | None = None,
    ) -> dict:
        agent = self.get_agent(agent_id)
        if not agent:
            return {"allowed": False, "reason": "agent_not_registered", "agent_id": agent_id}
        if agent.get("status") != "online":
            return {"allowed": False, "reason": "agent_offline", "agent": agent}
        if not agent.get("ready", False):
            return {"allowed": False, "reason": "agent_not_ready", "agent": agent}

        # ── circuit breaker check ──
        circuit = self._circuit_snapshot(agent_id)
        if circuit["state"] == "open":
            if _now_ts() < (circuit["open_until_ts"] or 0):
                return {
                    "allowed": False,
                    "reason": "circuit_open",
                    "circuit_state": circuit["state"],
                    "failure_count": circuit["failure_count"],
                    "open_until_ts": circuit["open_until_ts"],
                    "agent": agent,
                }
            # Recovery window passed → transition to half_open
            with self._circuits_lock:
                rec = self._circuits.get(self._circuit_key(agent_id))
                if rec and rec["state"] == "open":
                    rec["state"] = "half_open"

        required = _skill_set(required_skills)
        available = _skill_set(agent.get("skills", []))
        if required and not required.issubset(available):
            return {
                "allowed": False,
                "reason": "skill_mismatch",
                "required_skills": sorted(required),
                "agent": agent,
            }
        active_tasks = int(agent.get("active_tasks", 0) or 0)
        max_concurrency = int(agent.get("max_concurrency", 1) or 1)
        if active_tasks >= max_concurrency:
            return {
                "allowed": False,
                "reason": "concurrency_exhausted",
                "active_tasks": active_tasks,
                "max_concurrency": max_concurrency,
                "agent": agent,
            }
        resource_decision = self._evaluate_resources(
            agent.get("resources", {}),
            resource_requirements or {},
        )
        if not resource_decision["allowed"]:
            resource_decision["agent"] = agent
            return resource_decision
        return {"allowed": True, "reason": "ok", "agent": agent}

    @staticmethod
    def _system_value(resources: dict, field: str):
        system = resources.get("system", {}) if isinstance(resources.get("system"), dict) else {}
        if field in system:
            return system.get(field)
        return resources.get(field)

    @classmethod
    def _evaluate_resources(cls, resources: dict, requirements: dict) -> dict:
        resource_state = str(resources.get("resource_state", "ok")).lower()
        if resource_state == "critical":
            return {"allowed": False, "reason": "resource_critical", "resource_state": resource_state}

        checks = [
            ("max_cpu_percent", cls._system_value(resources, "cpu_percent"), "cpu_too_busy"),
            ("max_memory_percent", cls._system_value(resources, "memory_percent"), "memory_too_busy"),
            ("max_disk_percent", cls._system_value(resources, "disk_percent"), "disk_too_busy"),
        ]
        for requirement_key, actual, reason in checks:
            limit = _float_value(requirements.get(requirement_key))
            actual_value = _float_value(actual)
            if limit is not None and actual_value is not None and actual_value > limit:
                return {
                    "allowed": False,
                    "reason": reason,
                    "actual": actual_value,
                    "limit": limit,
                }

        gpus = resources.get("gpu") or resources.get("gpus") or []
        if isinstance(gpus, dict):
            gpus = [gpus]
        min_gpu_count = int(requirements.get("min_gpu_count") or 0)
        if min_gpu_count and len(gpus) < min_gpu_count:
            return {
                "allowed": False,
                "reason": "gpu_count_insufficient",
                "actual": len(gpus),
                "required": min_gpu_count,
            }

        min_vram_gb = _float_value(requirements.get("min_gpu_vram_gb"))
        if min_vram_gb is not None:
            has_vram = False
            for gpu in gpus:
                total_mb = _float_value(gpu.get("memory_total_mb") or gpu.get("vram_total_mb"))
                used_mb = _float_value(gpu.get("memory_used_mb") or gpu.get("vram_used_mb") or 0)
                free_gb = ((total_mb or 0) - (used_mb or 0)) / 1024
                if free_gb >= min_vram_gb:
                    has_vram = True
                    break
            if not has_vram:
                return {
                    "allowed": False,
                    "reason": "gpu_vram_insufficient",
                    "required_free_gb": min_vram_gb,
                }

        max_gpu_memory = _float_value(requirements.get("max_gpu_memory_percent"))
        max_gpu_util = _float_value(requirements.get("max_gpu_utilization_percent"))
        for gpu in gpus:
            memory_percent = _float_value(gpu.get("memory_percent") or gpu.get("vram_percent"))
            utilization = _float_value(gpu.get("utilization_percent") or gpu.get("gpu_percent"))
            if max_gpu_memory is not None and memory_percent is not None and memory_percent > max_gpu_memory:
                return {
                    "allowed": False,
                    "reason": "gpu_memory_too_busy",
                    "actual": memory_percent,
                    "limit": max_gpu_memory,
                }
            if max_gpu_util is not None and utilization is not None and utilization > max_gpu_util:
                return {
                    "allowed": False,
                    "reason": "gpu_too_busy",
                    "actual": utilization,
                    "limit": max_gpu_util,
                }

        return {"allowed": True, "reason": "ok"}

    def summary(self) -> dict:
        agents = self.list_agents()
        online = [agent for agent in agents if agent.get("status") == "online"]
        skills = {}
        for agent in online:
            for skill in agent.get("skills", []):
                skills[skill] = skills.get(skill, 0) + 1
        return {
            "agents_total": len(agents),
            "agents_online": len(online),
            "agents_offline": len(agents) - len(online),
            "skills": skills,
            "agents": agents,
        }

    def dashboard_html(self) -> str:
        summary = self.summary()
        rows = []
        for agent in summary["agents"]:
            resources = agent.get("resources", {})
            system = resources.get("system", {}) if isinstance(resources.get("system"), dict) else {}
            circuit = self._circuit_snapshot(agent.get("agent_id", ""))
            circuit_badge = (
                f'<span style="color:red;font-weight:bold">⬤ {circuit["state"]}</span>'
                if circuit["state"] == "open"
                else f'<span style="color:orange">◐ {circuit["state"]}</span>'
                if circuit["state"] == "half_open"
                else f'<span style="color:green">● {circuit["state"]}</span>'
            )
            rows.append(
                "<tr>"
                f"<td>{agent.get('agent_id')}</td>"
                f"<td>{agent.get('role') or ''}</td>"
                f"<td>{agent.get('status')}</td>"
                f"<td>{agent.get('ready')}</td>"
                f"<td>{', '.join(agent.get('skills', []))}</td>"
                f"<td>{agent.get('active_tasks', 0)}/{agent.get('max_concurrency', 1)}</td>"
                f"<td>{resources.get('resource_state', 'unknown')}</td>"
                f"<td>{system.get('cpu_percent', resources.get('cpu_percent', ''))}</td>"
                f"<td>{system.get('memory_percent', resources.get('memory_percent', ''))}</td>"
                f"<td>{circuit_badge} ({circuit['failure_count']})</td>"
                f"<td>{agent.get('last_heartbeat_at', '')}</td>"
                "</tr>"
            )
        return f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>A2A Supervisor</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 24px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
    th {{ background: #f4f6f8; }}
    .summary {{ margin-bottom: 16px; }}
  </style>
</head>
<body>
  <h1>A2A Supervisor</h1>
  <div class="summary">
    <strong>Total:</strong> {summary["agents_total"]}
    <strong>Online:</strong> {summary["agents_online"]}
    <strong>Offline:</strong> {summary["agents_offline"]}
  </div>
  <table>
    <thead>
      <tr>
        <th>Agent</th><th>Role</th><th>Status</th><th>Ready</th><th>Skills</th>
        <th>Tasks</th><th>Resource</th><th>CPU %</th><th>Memory %</th><th>Circuit</th><th>Heartbeat</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</body>
</html>
"""


class SupervisorClient:
    """HTTP client with the same small surface TaskPool/Agent need."""

    def __init__(self, base_url: str, *, timeout: float = 3.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)

    @classmethod
    def from_env(cls) -> "SupervisorClient | None":
        base_url = os.environ.get("A2A_SUPERVISOR_URL")
        if not base_url:
            return None
        return cls(base_url, timeout=float(os.environ.get("A2A_SUPERVISOR_TIMEOUT", "3")))

    def _post(self, path: str, payload: dict):
        try:
            import requests

            response = requests.post(
                f"{self.base_url}{path}",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            return {
                "allowed": False,
                "submitted": False,
                "registered": False,
                "reason": "supervisor_unavailable",
                "error": str(exc),
            }

    def register_agent(self, payload: dict) -> dict:
        return self._post("/agents/register", payload)

    def heartbeat(self, agent_id: str, payload: dict) -> dict:
        return self._post(f"/agents/{agent_id}/heartbeat", payload)

    def evaluate_claim(
        self,
        agent_id: str,
        *,
        required_skills: Any = None,
        resource_requirements: dict | None = None,
    ) -> dict:
        return self._post(
            f"/agents/{agent_id}/can-claim",
            {
                "required_skills": required_skills,
                "resource_requirements": resource_requirements or {},
            },
        )

    def task_started(self, agent_id: str, *, task_id: str = None, work_item: str = None) -> dict | None:
        return self._post(
            f"/agents/{agent_id}/task-started",
            {"task_id": task_id, "work_item": work_item},
        )

    def task_finished(self, agent_id: str, *, work_item: str = None) -> dict | None:
        return self._post(
            f"/agents/{agent_id}/task-finished",
            {"work_item": work_item},
        )


def supervisor_from_env():
    return SupervisorClient.from_env() or SupervisorStore.from_env()


def build_supervisor_app(store: SupervisorStore | None = None) -> FastAPI:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse

    store = store or SupervisorStore.from_env()
    app = FastAPI(title="A2A Supervisor")

    @app.get("/health")
    async def health():
        return {"status": "ok", "store": str(store.path)}

    @app.get("/summary")
    async def summary():
        return store.summary()

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard():
        return HTMLResponse(store.dashboard_html())

    @app.post("/agents/register")
    async def register_agent(payload: dict):
        try:
            return store.register_agent(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/agents")
    async def list_agents(include_offline: bool = True):
        return {"agents": store.list_agents(include_offline=include_offline)}

    @app.get("/agents/{agent_id}")
    async def get_agent(agent_id: str):
        agent = store.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="agent not found")
        return agent

    @app.post("/agents/{agent_id}/heartbeat")
    async def heartbeat(agent_id: str, payload: dict):
        try:
            return store.heartbeat(agent_id, payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/agents/{agent_id}/can-claim")
    async def can_claim(agent_id: str, payload: dict):
        return store.evaluate_claim(
            agent_id,
            required_skills=payload.get("required_skills") or payload.get("required_skill"),
            resource_requirements=payload.get("resource_requirements") or {},
        )

    @app.post("/agents/{agent_id}/task-started")
    async def task_started(agent_id: str, payload: dict):
        agent = store.task_started(
            agent_id,
            task_id=payload.get("task_id"),
            work_item=payload.get("work_item"),
        )
        if not agent:
            raise HTTPException(status_code=404, detail="agent not found")
        return agent

    @app.post("/agents/{agent_id}/task-finished")
    async def task_finished(agent_id: str, payload: dict):
        agent = store.task_finished(agent_id, work_item=payload.get("work_item"))
        if not agent:
            raise HTTPException(status_code=404, detail="agent not found")
        return agent

    return app


def parse_args():
    import argparse

    parser = argparse.ArgumentParser(description="Run A2A Supervisor service.")
    parser.add_argument("--host", default=os.environ.get("A2A_SUPERVISOR_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("A2A_SUPERVISOR_PORT", "8030")))
    parser.add_argument("--path", default=os.environ.get("A2A_SUPERVISOR_PATH"))
    return parser.parse_args()


if __name__ == "__main__":
    import uvicorn

    args = parse_args()
    supervisor_store = SupervisorStore(args.path)
    uvicorn.run(build_supervisor_app(supervisor_store), host=args.host, port=args.port)

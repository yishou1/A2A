from __future__ import annotations

import hashlib
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

from a2a_protocol.messages import is_success_response
from supervisor import SupervisorStore, supervisor_from_env
from workflow_state_store import utc_now_iso


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _now_ts() -> float:
    return time.time()


def _iso_from_ts(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


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


class JsonTaskPool:
    """
    File-backed crowd task pool used by Commander and Agents.

    Supports optional Redis distributed lock for cross-machine mutual
    exclusion, Supervisor-backed heartbeat checking during result wait,
    and per-Agent circuit breaker recording on result submission.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        lease_seconds: float = 60.0,
        supervisor: SupervisorStore | None = None,
        supervisor_required: bool | None = None,
        distributed_lock: "RedisDistributedLock | None" = None,
        heartbeat_check_interval: float | None = None,
    ):
        default_path = Path(__file__).resolve().parent / ".a2a_state" / "task_pool.json"
        self.path = Path(path or os.environ.get("A2A_TASK_POOL_PATH") or default_path)
        self.lease_seconds = float(lease_seconds)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._thread_lock = threading.RLock()
        self.supervisor = supervisor if supervisor is not None else supervisor_from_env()
        self.supervisor_required = (
            bool(supervisor_required)
            if supervisor_required is not None
            else os.environ.get("A2A_SUPERVISOR_REQUIRED", "false").lower()
            in {"1", "true", "yes", "on"}
        )
        # ── distributed lock (optional, for cross-machine safety) ──
        self._distributed_lock = distributed_lock
        self._distributed_lock_handle: object | None = None
        self._distributed_lock_depth: int = 0
        # ── heartbeat check during wait_for_result ──
        self._heartbeat_check_interval = float(
            heartbeat_check_interval
            if heartbeat_check_interval is not None
            else os.environ.get("A2A_CROWD_HEARTBEAT_CHECK_INTERVAL", "2")
        )

    @classmethod
    def from_env(cls) -> "JsonTaskPool":
        return cls(
            os.environ.get("A2A_TASK_POOL_PATH"),
            lease_seconds=float(os.environ.get("A2A_TASK_LEASE_SECONDS", "60")),
        )

    @contextmanager
    def _locked_state(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._thread_lock:
            # ── distributed lock path (cross-machine) ──
            if self._distributed_lock is not None:
                if self._distributed_lock_depth == 0:
                    handle = self._distributed_lock.acquire("a2a:task-pool:state")
                    self._distributed_lock_handle = handle
                self._distributed_lock_depth += 1
                try:
                    state = self._load_unlocked()
                    yield state
                    self._save_unlocked(state)
                finally:
                    self._distributed_lock_depth -= 1
                    if self._distributed_lock_depth == 0 and self._distributed_lock_handle is not None:
                        self._distributed_lock.release(self._distributed_lock_handle)
                        self._distributed_lock_handle = None
                return

            # ── file lock path (single-machine) ──
            with _CrossProcessFileLock(self.lock_path):
                state = self._load_unlocked()
                yield state
                self._save_unlocked(state)

    def _load_unlocked(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "tasks": {}}
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                state = json.load(handle)
        except json.JSONDecodeError:
            state = {"version": 1, "tasks": {}}
        state.setdefault("version", 1)
        state.setdefault("tasks", {})
        return state

    def _save_unlocked(self, state: dict) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.path)

    @staticmethod
    def _task_id_for_work_item(work_item: str) -> str:
        digest = hashlib.sha1(str(work_item).encode("utf-8")).hexdigest()[:12]
        return f"task-{digest}"

    @staticmethod
    def _required_skills_from_payload(payload: dict) -> list[str]:
        skills = _split_skill_values(payload.get("required_skills"))
        skill = (
            payload.get("required_skill")
            or payload.get("activity_skill")
            or (payload.get("activity") or {}).get("required_skill")
            or payload.get("command")
        )
        if skill and skill not in skills:
            skills.insert(0, skill)
        return skills

    @staticmethod
    def _task_matches_agent(task: dict, agent_skills: Any) -> bool:
        required = _skill_set(task.get("required_skills") or task.get("required_skill"))
        if not required:
            return True
        available = _skill_set(agent_skills)
        if not available:
            return False
        return required.issubset(available)

    @staticmethod
    def _expire_task_claims(task: dict, now_ts: float) -> bool:
        changed = False
        active_claims = []
        for claim in task.get("claims", []):
            if claim.get("status") in TERMINAL_STATUSES:
                active_claims.append(claim)
                continue
            lease_until_ts = float(claim.get("lease_until_ts") or 0)
            if lease_until_ts and lease_until_ts < now_ts:
                claim["status"] = "expired"
                claim["expired_at"] = utc_now_iso()
                changed = True
            active_claims.append(claim)
        task["claims"] = active_claims
        if task.get("status") in {"claimed", "running"}:
            live = [
                claim
                for claim in active_claims
                if claim.get("status") in {"claimed", "running"}
            ]
            if not live and not task.get("results"):
                task["status"] = "pending"
                task["updated_at"] = utc_now_iso()
                changed = True
        return changed

    def publish(
        self,
        payload: dict,
        *,
        stream: bool = False,
        max_claims: int = 1,
        min_results: int = 1,
    ) -> dict:
        work_item = payload.get("work_item")
        if not work_item:
            raise ValueError("crowd task payload must include work_item")
        task_id = self._task_id_for_work_item(work_item)
        required_skills = self._required_skills_from_payload(payload)
        now = utc_now_iso()
        with self._locked_state() as state:
            tasks = state.setdefault("tasks", {})
            existing = tasks.get(task_id)
            if existing and existing.get("status") not in TERMINAL_STATUSES:
                existing["payload"] = deepcopy(payload)
                existing["required_skill"] = required_skills[0] if required_skills else None
                existing["required_skills"] = list(required_skills)
                existing["stream"] = bool(stream)
                existing["max_claims"] = max(1, int(max_claims or 1))
                existing["min_results"] = max(1, int(min_results or 1))
                existing["resource_requirements"] = deepcopy(payload.get("resource_requirements", {}))
                existing["updated_at"] = now
                return deepcopy(existing)
            if existing and existing.get("status") == "completed":
                return deepcopy(existing)
            task = {
                "task_id": task_id,
                "workflow_id": payload.get("workflow_id"),
                "work_item": work_item,
                "activity_id": payload.get("activity_id") or payload.get("activatity_id"),
                "activity_index": payload.get("activity_index") or payload.get("activatity_index"),
                "activity_skill": payload.get("activity_skill") or payload.get("required_skill"),
                "required_skill": required_skills[0] if required_skills else None,
                "required_skills": list(required_skills),
                "input": deepcopy(payload.get("input", {})),
                "output_hint": payload.get("output_hint"),
                "resource_requirements": deepcopy(payload.get("resource_requirements", {})),
                "payload": deepcopy(payload),
                "stream": bool(stream),
                "status": "pending",
                "max_claims": max(1, int(max_claims or 1)),
                "min_results": max(1, int(min_results or 1)),
                "claims": [],
                "results": [],
                "created_at": now,
                "updated_at": now,
            }
            tasks[task_id] = task
            return deepcopy(task)

    def list_available(self, *, agent_skills: Any = None, workflow_id: str | None = None) -> list[dict]:
        now = _now_ts()
        with self._locked_state() as state:
            available = []
            for task in state.setdefault("tasks", {}).values():
                self._expire_task_claims(task, now)
                if workflow_id and task.get("workflow_id") != workflow_id:
                    continue
                if task.get("status") in TERMINAL_STATUSES:
                    continue
                if not self._task_matches_agent(task, agent_skills):
                    continue
                active_claims = [
                    claim
                    for claim in task.get("claims", [])
                    if claim.get("status") in {"claimed", "running"}
                ]
                if len(active_claims) >= int(task.get("max_claims") or 1):
                    continue
                available.append(deepcopy(task))
            return available

    def claim_next(
        self,
        *,
        agent_id: str,
        agent_skills: Any,
        workflow_id: str | None = None,
        lease_seconds: float | None = None,
    ) -> dict:
        available = self.list_available(agent_skills=agent_skills, workflow_id=workflow_id)
        if not available:
            return {"claimed": False, "reason": "no_available_task"}
        last_rejection = None
        for task in available:
            result = self.claim(
                task["task_id"],
                agent_id=agent_id,
                agent_skills=agent_skills,
                lease_seconds=lease_seconds,
            )
            if result.get("claimed"):
                return result
            last_rejection = result
        return last_rejection or {"claimed": False, "reason": "no_available_task"}

    def claim(
        self,
        task_id: str,
        *,
        agent_id: str,
        agent_skills: Any,
        lease_seconds: float | None = None,
    ) -> dict:
        now_ts = _now_ts()
        lease_seconds = float(lease_seconds if lease_seconds is not None else self.lease_seconds)
        with self._locked_state() as state:
            task = state.setdefault("tasks", {}).get(task_id)
            if not task:
                return {"claimed": False, "reason": "task_not_found"}
            self._expire_task_claims(task, now_ts)
            if task.get("status") in TERMINAL_STATUSES:
                return {"claimed": False, "reason": f"task_{task.get('status')}"}
            if not self._task_matches_agent(task, agent_skills):
                return {"claimed": False, "reason": "skill_mismatch"}
            supervisor_decision = self.supervisor.evaluate_claim(
                agent_id,
                required_skills=task.get("required_skills") or task.get("required_skill"),
                resource_requirements=task.get("resource_requirements") or {},
            )
            if not supervisor_decision.get("allowed"):
                reason = supervisor_decision.get("reason")
                if self.supervisor_required or reason != "agent_not_registered":
                    return {
                        "claimed": False,
                        "reason": reason or "supervisor_rejected",
                        "supervisor": supervisor_decision,
                    }
            active_claims = [
                claim
                for claim in task.get("claims", [])
                if claim.get("status") in {"claimed", "running"}
            ]
            if len(active_claims) >= int(task.get("max_claims") or 1):
                return {"claimed": False, "reason": "already_claimed"}
            claim_id = f"{task_id}:claim-{len(task.get('claims', [])) + 1}"
            claim = {
                "claim_id": claim_id,
                "agent_id": agent_id,
                "status": "claimed",
                "claimed_at": utc_now_iso(),
                "lease_until_ts": now_ts + lease_seconds,
                "lease_until": _iso_from_ts(now_ts + lease_seconds),
            }
            task.setdefault("claims", []).append(claim)
            task["status"] = "claimed"
            task["claimed_by"] = agent_id
            task["updated_at"] = utc_now_iso()
            self.supervisor.task_started(
                agent_id,
                task_id=task_id,
                work_item=task.get("work_item"),
            )
            claim_payload = deepcopy(task.get("payload", {}))
            claim_payload["crowd_task_id"] = task_id
            claim_payload["crowd_claim_id"] = claim_id
            claim_payload["crowd_claimed_by"] = agent_id
            return {
                "claimed": True,
                "task_id": task_id,
                "claim_id": claim_id,
                "lease_until": claim["lease_until"],
                "payload": claim_payload,
                "task": deepcopy(task),
            }

    def renew_claim(
        self,
        task_id: str,
        *,
        claim_id: str,
        agent_id: str,
        lease_seconds: float | None = None,
    ) -> dict:
        """Extend the lease of an active claim. Used by Agents for long-running tasks."""
        lease_seconds = float(lease_seconds if lease_seconds is not None else self.lease_seconds)
        now_ts = _now_ts()
        with self._locked_state() as state:
            task = state.setdefault("tasks", {}).get(task_id)
            if not task:
                return {"renewed": False, "reason": "task_not_found"}
            self._expire_task_claims(task, now_ts)
            for claim in task.get("claims", []):
                if claim.get("claim_id") != claim_id:
                    continue
                if claim.get("agent_id") != agent_id:
                    return {"renewed": False, "reason": "agent_mismatch"}
                if claim.get("status") in TERMINAL_STATUSES:
                    return {"renewed": False, "reason": f"claim_{claim.get('status')}"}
                new_lease_ts = now_ts + lease_seconds
                claim["lease_until_ts"] = new_lease_ts
                claim["lease_until"] = _iso_from_ts(new_lease_ts)
                claim["lease_renewed_at"] = utc_now_iso()
                claim["lease_renewals"] = claim.get("lease_renewals", 0) + 1
                task["updated_at"] = utc_now_iso()
                return {
                    "renewed": True,
                    "task_id": task_id,
                    "claim_id": claim_id,
                    "lease_until": claim["lease_until"],
                    "renewals": claim["lease_renewals"],
                }
            return {"renewed": False, "reason": "claim_not_found"}

    def submit_result(
        self,
        task_id: str,
        *,
        claim_id: str | None,
        agent_id: str,
        response: dict,
    ) -> dict:
        now_ts = _now_ts()
        with self._locked_state() as state:
            task = state.setdefault("tasks", {}).get(task_id)
            if not task:
                return {"submitted": False, "reason": "task_not_found"}

            # ── stale claim protection ──
            if claim_id:
                matching_claim = None
                for claim in task.get("claims", []):
                    if claim.get("claim_id") == claim_id:
                        matching_claim = claim
                        break
                if matching_claim is None:
                    return {"submitted": False, "reason": "claim_not_found"}
                if matching_claim.get("agent_id") != agent_id:
                    return {"submitted": False, "reason": "agent_mismatch"}
                if matching_claim.get("status") in TERMINAL_STATUSES:
                    return {
                        "submitted": False,
                        "reason": f"claim_{matching_claim.get('status')}",
                    }
                if matching_claim.get("status") == "expired":
                    return {"submitted": False, "reason": "claim_expired"}
                # Check lease hasn't expired (even if not yet marked expired)
                lease_until_ts = float(matching_claim.get("lease_until_ts") or 0)
                if lease_until_ts and lease_until_ts < now_ts:
                    self._expire_task_claims(task, now_ts)
                    # Clean up agent task tracking since claim is dead
                    self.supervisor.task_finished(agent_id, work_item=task.get("work_item"))
                    return {"submitted": False, "reason": "claim_lease_expired"}

            success = is_success_response(response)
            # ── extract structured error info for Commander-side classification ──
            resp_error_code = (response or {}).get("error_code", "")
            resp_error_category = "system" if resp_error_code in {
                "AGENT_UNAVAILABLE", "AGENT_TIMEOUT", "AGENT_NOT_READY",
                "AGENT_HEARTBEAT_LOST", "AGENT_HTTP_5XX",
            } else "business" if resp_error_code == "AGENT_BUSINESS_ERROR" else (
                "protocol" if resp_error_code == "AGENT_PROTOCOL_ERROR" else ""
            )
            result = {
                "claim_id": claim_id,
                "agent_id": agent_id,
                "status": "completed" if success else "failed",
                "response": deepcopy(response or {}),
                "submitted_at": utc_now_iso(),
                "error_code": resp_error_code or None,
                "error_category": resp_error_category or None,
            }
            task.setdefault("results", []).append(result)
            for claim in task.get("claims", []):
                if claim.get("claim_id") == claim_id:
                    claim["status"] = result["status"]
                    claim["submitted_at"] = result["submitted_at"]
                    break
            self.supervisor.task_finished(agent_id, work_item=task.get("work_item"))
            # ── circuit breaker: record success or failure ──
            # Only record system-level failures (not business errors) for circuit breaker
            if success:
                self.supervisor.record_agent_success(agent_id)
            else:
                error_code = (response or {}).get("error_code", "")
                if error_code in {"AGENT_BUSINESS_ERROR", "AGENT_PROTOCOL_ERROR"}:
                    # Business/protocol failures don't indicate agent health issues
                    pass
                else:
                    error_msg = (response or {}).get("error") or (response or {}).get("message") or "unknown"
                    self.supervisor.record_agent_failure(agent_id, error_message=str(error_msg))
            completed_count = sum(1 for item in task.get("results", []) if item.get("status") == "completed")
            failed_count = sum(1 for item in task.get("results", []) if item.get("status") == "failed")
            min_results = int(task.get("min_results") or 1)
            max_claims = int(task.get("max_claims") or 1)
            if completed_count >= min_results:
                task["status"] = "completed"
            elif completed_count + failed_count >= max_claims:
                task["status"] = "failed"
            else:
                task["status"] = "pending"
            task["updated_at"] = utc_now_iso()
            return {"submitted": True, "task": deepcopy(task)}

    @staticmethod
    def _aggregate_task_response(task: dict) -> dict | None:
        results = [item for item in task.get("results", []) if item.get("status") == "completed"]
        if not results:
            return None
        responses = [deepcopy(item.get("response") or {}) for item in results]
        if len(responses) == 1:
            response = responses[0]
        else:
            response = deepcopy(responses[-1])
            response["parallel_results"] = responses
        response.setdefault("crowd_task_id", task.get("task_id"))
        response.setdefault("work_item", task.get("work_item"))
        return response

    def get_task(self, task_id: str) -> dict | None:
        with self._locked_state() as state:
            task = state.setdefault("tasks", {}).get(task_id)
            return deepcopy(task) if task else None

    def get_task_by_work_item(self, work_item: str) -> dict | None:
        return self.get_task(self._task_id_for_work_item(work_item))

    def wait_for_result(
        self,
        work_item: str,
        *,
        timeout_seconds: float,
        poll_interval: float = 0.5,
    ) -> tuple[bool, dict | None, dict | None]:
        deadline = _now_ts() + float(timeout_seconds)
        last_task = None
        last_heartbeat_check = 0.0
        while True:
            task = self.get_task_by_work_item(work_item)
            last_task = task
            if task:
                response = self._aggregate_task_response(task)
                if response:
                    return True, response, task
                if task.get("status") in {"failed", "cancelled"}:
                    return False, None, task

                # ── heartbeat check: expire claims from offline agents ──
                now = _now_ts()
                if self.supervisor is not None and now - last_heartbeat_check >= self._heartbeat_check_interval:
                    last_heartbeat_check = now
                    expired_any = False
                    for claim in task.get("claims", []):
                        if claim.get("status") in {"claimed", "running"}:
                            agent_id = claim.get("agent_id")
                            if agent_id and not self.supervisor.is_agent_online(agent_id):
                                claim["status"] = "expired"
                                claim["expired_at"] = utc_now_iso()
                                claim["expired_reason"] = "agent_offline_heartbeat_lost"
                                expired_any = True
                                # ── circuit breaker: record failure for offline agent ──
                                try:
                                    self.supervisor.record_agent_failure(
                                        agent_id,
                                        error_message="agent heartbeat lost during task execution",
                                    )
                                except Exception:
                                    pass
                    if expired_any:
                        # Check if task should revert to pending
                        live = [
                            c
                            for c in task.get("claims", [])
                            if c.get("status") in {"claimed", "running"}
                        ]
                        if not live and not task.get("results"):
                            with self._locked_state() as state:
                                t = state.setdefault("tasks", {}).get(task["task_id"])
                                if t:
                                    self._expire_task_claims(t, now)
                                    live_check = [
                                        c
                                        for c in t.get("claims", [])
                                        if c.get("status") in {"claimed", "running"}
                                    ]
                                    if not live_check and not t.get("results"):
                                        t["status"] = "pending"
                                        t["updated_at"] = utc_now_iso()

            if _now_ts() >= deadline:
                return False, None, last_task
            time.sleep(max(0.05, float(poll_interval)))

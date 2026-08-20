"""Small runtime helpers for near-real A2A workflow behavior.

The demo remains intentionally lightweight, but these helpers make it behave
more like a workflow-managed Agent: work item idempotency, workflow work-list
snapshots, and observable busy/idle state.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Any, Dict, List


@dataclass
class A2ARuntimeState:
    agent_name: str
    role: str
    agent_status: str = "idle"
    ready: bool = True
    started_at: float = field(default_factory=time.time)
    current_workflow_id: str | None = None
    current_work_item: str | None = None
    tasks_received_count: int = 0
    processed_task_count: int = 0
    failed_task_count: int = 0
    cache_hit_count: int = 0
    active_task_count: int = 0
    rejected_task_count: int = 0
    max_concurrent_tasks: int = 1
    last_error: str | None = None
    _task_response_cache: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    _stream_response_cache: Dict[str, List[str]] = field(default_factory=dict)
    _workflow_work_lists: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    _recovery_notices: List[Dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def work_item_from_payload(payload: Dict[str, Any]) -> str:
        return str(payload.get("work_item") or payload.get("task_id") or "work-item-001")

    def capture_work_list(self, payload: Dict[str, Any]) -> None:
        workflow_id = payload.get("workflow_id")
        work_list = payload.get("work_list")
        if workflow_id and isinstance(work_list, list):
            self._workflow_work_lists[str(workflow_id)] = deepcopy(work_list)

    def get_work_list(self, workflow_id: str) -> List[Dict[str, Any]]:
        return deepcopy(self._workflow_work_lists.get(workflow_id, []))

    def get_task_response(self, work_item: str) -> Dict[str, Any] | None:
        cached = self._task_response_cache.get(work_item)
        if cached is None:
            return None
        response = deepcopy(cached)
        response["cached"] = True
        self.cache_hit_count += 1
        return response

    def set_task_response(self, work_item: str, response: Dict[str, Any]) -> None:
        cached = deepcopy(response)
        cached["cached"] = False
        self._task_response_cache[work_item] = cached
        self.processed_task_count += 1

    def get_stream_events(self, work_item: str) -> List[str] | None:
        cached = self._stream_response_cache.get(work_item)
        return deepcopy(cached) if cached is not None else None

    def set_stream_events(self, work_item: str, events: List[str]) -> None:
        self._stream_response_cache[work_item] = list(events)

    def mark_busy(self, workflow_id: str | None, work_item: str | None) -> None:
        self.agent_status = "busy"
        self.current_workflow_id = workflow_id
        self.current_work_item = work_item
        self.tasks_received_count += 1
        self.active_task_count += 1

    def try_mark_busy(self, workflow_id: str | None, work_item: str | None) -> bool:
        """Atomically reserve the single stateful processing slot.

        The FastAPI event loop calls this synchronous method without an await
        between checking and incrementing, so competing requests cannot both
        reserve the TrackStore update slot.
        """

        if not self.ready or self.active_task_count >= self.max_concurrent_tasks:
            self.rejected_task_count += 1
            return False
        self.mark_busy(workflow_id, work_item)
        return True

    def mark_idle(self) -> None:
        self.agent_status = "idle"
        self.current_workflow_id = None
        self.current_work_item = None
        self.active_task_count = max(0, self.active_task_count - 1)

    def mark_error(self, error: str | None = None) -> None:
        self.agent_status = "error"
        self.failed_task_count += 1
        self.last_error = error

    def set_ready(self, ready: bool) -> None:
        self.ready = ready

    def reset_runtime(self) -> None:
        self.agent_status = "idle"
        self.ready = True
        self.current_workflow_id = None
        self.current_work_item = None
        self.tasks_received_count = 0
        self.processed_task_count = 0
        self.failed_task_count = 0
        self.cache_hit_count = 0
        self.active_task_count = 0
        self.rejected_task_count = 0
        self.last_error = None
        self._task_response_cache.clear()
        self._stream_response_cache.clear()
        self._workflow_work_lists.clear()
        self._recovery_notices.clear()

    def notify_recovery(self, notice: Dict[str, Any]) -> Dict[str, Any]:
        notice = notice or {}
        record = {
            "received_at": datetime.now(timezone.utc).isoformat(),
            "workflow_id": notice.get("workflow_id"),
            "action": notice.get("action", "resume"),
            "reason": notice.get("reason"),
            "detail": deepcopy(notice.get("detail")) if notice.get("detail") else None,
            "reset_cache": bool(notice.get("reset_cache", False)),
        }
        self._recovery_notices.append(record)
        self._recovery_notices = self._recovery_notices[-100:]
        if record["reset_cache"]:
            workflow_id = record["workflow_id"]
            if workflow_id:
                self._task_response_cache = {
                    key: value
                    for key, value in self._task_response_cache.items()
                    if value.get("workflow_id") != workflow_id
                }
            else:
                self._task_response_cache.clear()
            self._stream_response_cache.clear()
        self.ready = True
        return {
            "acknowledged": True,
            "agent": self.agent_name,
            "role": self.role,
            "ready": self.ready,
            "recovery": deepcopy(record),
        }

    def recovery_notices(self) -> List[Dict[str, Any]]:
        return deepcopy(self._recovery_notices)

    def snapshot(self, algorithm_provider: str | None = None) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "role": self.role,
            "agent_status": self.agent_status,
            "ready": self.ready,
            "current_workflow_id": self.current_workflow_id,
            "current_work_item": self.current_work_item,
            "tasks_received_count": self.tasks_received_count,
            "processed_task_count": self.processed_task_count,
            "failed_task_count": self.failed_task_count,
            "cache_hit_count": self.cache_hit_count,
            "active_task_count": self.active_task_count,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "available_task_slots": max(0, self.max_concurrent_tasks - self.active_task_count),
            "rejected_task_count": self.rejected_task_count,
            "last_error": self.last_error,
            "cached_work_item_count": len(self._task_response_cache),
            "cached_stream_count": len(self._stream_response_cache),
            "workflow_work_list_count": len(self._workflow_work_lists),
            "recovery_notice_count": len(self._recovery_notices),
            "algorithm_provider": algorithm_provider,
        }

    def metrics_snapshot(self) -> Dict[str, Any]:
        return {
            "agent": self.agent_name,
            "role": self.role,
            "ready": self.ready,
            "agent_status": self.agent_status,
            "uptime_seconds": round(time.time() - self.started_at, 3),
            "tasks_received": self.tasks_received_count,
            "tasks_completed": self.processed_task_count,
            "tasks_failed": self.failed_task_count,
            "stream_requests": len(self._stream_response_cache),
            "cache_hits": self.cache_hit_count,
            "active_tasks": self.active_task_count,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "available_task_slots": max(0, self.max_concurrent_tasks - self.active_task_count),
            "tasks_rejected": self.rejected_task_count,
            "last_error": self.last_error,
            "rejected_task_count": self.rejected_task_count,
            "last_work_item": self.current_work_item,
        }

    def export_persistent_state(self) -> Dict[str, Any]:
        """Return restart-safe runtime state.

        Busy/current fields are intentionally excluded. After a restart the
        Agent should come back as idle and let the orchestrator retry any work
        item whose response was not cached.
        """

        return {
            "agent_name": self.agent_name,
            "role": self.role,
            "ready": self.ready,
            "tasks_received_count": self.tasks_received_count,
            "processed_task_count": self.processed_task_count,
            "failed_task_count": self.failed_task_count,
            "cache_hit_count": self.cache_hit_count,
            "rejected_task_count": self.rejected_task_count,
            "last_error": self.last_error,
            "task_response_cache": deepcopy(self._task_response_cache),
            "stream_response_cache": deepcopy(self._stream_response_cache),
            "workflow_work_lists": deepcopy(self._workflow_work_lists),
            "recovery_notices": deepcopy(self._recovery_notices),
        }

    def restore_persistent_state(self, state: Dict[str, Any]) -> None:
        self.agent_status = "idle"
        self.ready = bool(state.get("ready", True))
        self.current_workflow_id = None
        self.current_work_item = None
        self.tasks_received_count = int(state.get("tasks_received_count", 0) or 0)
        self.processed_task_count = int(state.get("processed_task_count", 0) or 0)
        self.failed_task_count = int(state.get("failed_task_count", 0) or 0)
        self.cache_hit_count = int(state.get("cache_hit_count", 0) or 0)
        self.active_task_count = 0
        self.rejected_task_count = int(state.get("rejected_task_count", 0) or 0)
        self.last_error = state.get("last_error")
        self._task_response_cache = deepcopy(state.get("task_response_cache", {}) or {})
        self._stream_response_cache = deepcopy(state.get("stream_response_cache", {}) or {})
        self._workflow_work_lists = deepcopy(state.get("workflow_work_lists", {}) or {})
        self._recovery_notices = deepcopy(state.get("recovery_notices", []) or [])[-100:]

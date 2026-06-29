"""Small runtime helpers for near-real A2A workflow behavior.

The demo remains intentionally lightweight, but these helpers make it behave
more like a workflow-managed Agent: work item idempotency, workflow work-list
snapshots, and observable busy/idle state.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
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
    last_error: str | None = None
    _task_response_cache: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    _stream_response_cache: Dict[str, List[str]] = field(default_factory=dict)
    _workflow_work_lists: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)

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

    def mark_idle(self) -> None:
        self.agent_status = "idle"
        self.current_workflow_id = None
        self.current_work_item = None
        self.active_task_count = max(0, self.active_task_count - 1)

    def mark_error(self, error: str | None = None) -> None:
        self.agent_status = "error"
        self.failed_task_count += 1
        self.last_error = error
        self.active_task_count = max(0, self.active_task_count - 1)

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
        self.last_error = None
        self._task_response_cache.clear()
        self._stream_response_cache.clear()
        self._workflow_work_lists.clear()

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
            "last_error": self.last_error,
            "cached_work_item_count": len(self._task_response_cache),
            "cached_stream_count": len(self._stream_response_cache),
            "workflow_work_list_count": len(self._workflow_work_lists),
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
            "last_error": self.last_error,
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
            "last_error": self.last_error,
            "task_response_cache": deepcopy(self._task_response_cache),
            "stream_response_cache": deepcopy(self._stream_response_cache),
            "workflow_work_lists": deepcopy(self._workflow_work_lists),
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
        self.last_error = state.get("last_error")
        self._task_response_cache = deepcopy(state.get("task_response_cache", {}) or {})
        self._stream_response_cache = deepcopy(state.get("stream_response_cache", {}) or {})
        self._workflow_work_lists = deepcopy(state.get("workflow_work_lists", {}) or {})
